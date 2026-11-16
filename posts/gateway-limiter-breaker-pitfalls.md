---
title: 自己写网关踩的两个坑：限流没生效，熔断不触发
date: 2026-11-16
summary: 压测 200 次一个 429 都没有，后端挂掉后 8 次请求照样 502。两个问题都不是代码写错，而是漏了一层结构性的东西。
tags: Go, 网关, 限流, 熔断
series: Go 服务端
---

用 Go 写了个网关，限流用令牌桶，熔断用三态机。代码编译一次过，单元测试也懒得写就跑去验证——结果两个功能全哑了。

排查过程和根因都挺典型，记一下。

## 坑一：限流完全不生效

配置里写的是 50 rps、burst 100。验证方式很朴素，连发 200 次健康检查：

```bash
for i in $(seq 1 200); do
  curl -s -o /dev/null -w "%{http_code}\n" http://localhost:9000/health
done | sort | uniq -c
```

期望看到一批 429，实际是：

```text
    200 200
```

全部 200，一个限流都没有。第一反应是令牌桶算错了，但重读代码没问题——`rate.NewLimiter(50, 100)` 加 `Allow()`，标准用法。

问题在别处。启动代码长这样：

```go
mux := http.NewServeMux()
mux.Handle("/health", healthHandler)     // 直接注册
mux.Handle("/metrics", metrics.Handler()) // 直接注册
mux.Handle("/", chain(router, ...))       // 这里才套中间件
```

**中间件只包住了 `mux` 的 `/` 分支，`/health` 走的是另一个独立分支，压根没经过限流。**

这也解释了为什么日志里也看不到健康检查的访问记录——它连日志中间件都绕过了。

改法是把健康检查从 mux 里拿掉，放进路由分发函数：

```go
func dispatch(w http.ResponseWriter, r *http.Request) {
    if r.URL.Path == "/health" {
        w.Write([]byte(`{"status":"ok"}`))
        return
    }
    // 走路由匹配和代理...
}

mux := http.NewServeMux()
mux.Handle("/metrics", metrics.Handler())  // 唯一豁免
mux.Handle("/", chain(http.HandlerFunc(dispatch), logging, metricsMW, limiter, auth))
```

`/metrics` 是刻意豁免的——它必须绕过限流，否则 Prometheus 抓取会被自己限掉，监控直接瞎掉。

改完再压，结果正常了：

```text
200: 106
429: 294
```

106 大约是 burst 100 加上限流期间补充的几个令牌。

**教训**：`ServeMux` 上注册的每一条独立路径都是中间件的保护缺口。注册的路径越多，漏得越多。正确的结构是**只留一个入口，其余全部在链内部按路径分发**。

## 坑二：熔断器永远不打开

熔断的逻辑是"连续失败 5 次后打开，打开期间直接返回 503"。验证方法是把后端停掉，然后连续请求。

结果 8 次请求全部是 502：

```text
第 1 次: 502
...
第 8 次: 502
```

502 是反向代理连不上后端时的默认错误，说明请求确实到了代理层。但熔断器的状态指标一直是 0（关闭）：

```text
gateway_breaker_state{route="/review"} 0
```

原因很直白：`Guard` 只做了"放行判断"，没有做"结果记账"。

```go
// 修改前
func (b *Breaker) Guard(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if !b.Allow() {          // 打开就拒绝
            w.WriteHeader(503)
            return
        }
        next.ServeHTTP(w, r)     // 放行，但结果没人看
    })
}
```

`Allow()` 在关闭状态下永远返回 true，`Failure()` 一次都没被调用，计数器停在 0，阈值永远不会到达。

修法是包一层状态记录器，请求结束后看响应码：

```go
func (b *Breaker) Guard(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if !b.Allow() {
            http.Error(w, "upstream unavailable", 503)
            return
        }
        sw := NewStatusWriter(w)
        next.ServeHTTP(sw, r)
        if sw.Status >= 500 {
            b.Failure()
        } else {
            b.Success()
        }
    })
}
```

改完立刻正常：

```text
第 1-5 次: 502     ← 真实调用，累计失败
第 6-8 次: 503     ← 熔断打开，不再打后端
```

后端恢复后请求回到 200，熔断自动关闭。

**教训**：熔断器不是"拦截器"，是"带状态的观测器"。它必须同时具备三个入口——**放行判断、成功上报、失败上报**。只实现第一个，就只是一个会无条件放行的装饰器。

## 两个坑的共同点

都不是"代码写错了"，而是**结构上少了一层**：

- 限流少的是"所有路径都经过中间件"这个约束
- 熔断少的是"结果回传给状态机"这条回路

这类问题编译期、甚至类型检查都发现不了，只有真的跑一次、真的看一眼计数，才会暴露。写完一个中间件就立刻验证一次，比攒到最后一起测要省事得多。
