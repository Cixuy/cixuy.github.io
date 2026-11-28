---
title: Go 的 context 取消传不下去：三个断链点和 errgroup 的正确用法
date: 2026-12-02
summary: 上游超时了，下游还在跑，goroutine 越堆越多——context 没断的原因通常只有三种：忘了传、传了没检查、以及在 select 里选错了分支。
tags: Go, context, 并发, errgroup
---

「服务响应变慢了，goroutine 数量一直在涨」——这类问题里，`context` 经常是「传了，但没起作用」。取消信号不会自动扩散，它需要每一层都**主动检查**。

## 取消的基本机制：两个检查点

`context` 的取消靠两个东西实现：

1. **`Done()` 通道**：取消时关闭，所有监听它的地方同时收到信号
2. **`Err()`**：取消后返回 `context.Canceled` 或 `context.DeadlineExceeded`

底层是一条链：父 context 取消 → 遍历 `children` → 逐个 cancel。所以只要**父 context 传了下去**，取消就能传播到整棵树。

问题几乎都出在「传下去」和「检查」这两个动作上。

## 断链点一：派生 context 时用了 Background

```go
func handle(w http.ResponseWriter, r *http.Request) {
    // ❌ 丢掉了请求的 ctx，上游超时跟这里完全没关系了
    go doWork(context.Background(), r)
}

func doWork(ctx context.Context, r *http.Request) { ... }
```

`context.Background()` 是一条**新的根**，跟 `r.Context()` 没有任何父子关系。`http.Server` 在超时或客户端断开时会 cancel `r.Context()`，但 `doWork` 用的那条根本收不到。

正确写法：

```go
go doWork(r.Context(), r)   // 或者显式 ctx, cancel := context.WithTimeout(...)
```

同样的坑还有 `context.TODO()`——它跟 Background 一样是根节点，只是语义上表示「还没想好」。生产代码里出现 `TODO` 基本等于埋雷。

## 断链点二：传了 ctx 但函数体里从不检查

```go
func doWork(ctx context.Context, items []Item) error {
    for _, it := range items {          // ❌ ctx 完全没被用到
        if err := process(it); err != nil {
            return err
        }
    }
    return nil
}
```

ctx 只是被透传给了下游，而如果这一层自己是个纯计算/IO 循环，取消信号就卡在这里。**处于中间层的阻塞循环必须自己检查**：

```go
for _, it := range items {
    select {
    case <-ctx.Done():
        return ctx.Err()                // 把取消原因往上抛
    default:
    }
    if err := process(it); err != nil {
        return err
    }
}
```

注意 `select` 里要有 `default`，否则会阻塞。这是最常见的写法错误。

## 断链点三：select 里漏了 Done 分支

```go
select {
case res := <-ch:
    return res, nil
case <-time.After(30 * time.Second):   // ❌ 上游取消了这里还在干等 30s
    return nil, errors.New("timeout")
}
```

`time.After` 自己成了一个独立的取消源，跟 ctx 无关。正确写法是让两者竞争：

```go
select {
case res := <-ch:
    return res, nil
case <-ctx.Done():
    return nil, ctx.Err()
case <-time.After(30 * time.Second):
    return nil, errors.New("timeout")
}
```

顺带一个性能细节：`time.After` 在循环里用会每轮创建一个 Timer，短循环里应该是 `t := time.NewTimer(...); defer t.Stop()`。

## 阻塞调用怎么办：context 管不了非 context 的 API

```go
resp, err := http.DefaultClient.Do(req)     // ❌ 没带 ctx
```

这类调用**必须**用带 ctx 的版本：

```go
req, _ := http.NewRequestWithContext(ctx, "GET", url, nil)
resp, err := http.DefaultClient.Do(req)
```

同理 `db.QueryContext`、`redis.GetContext`、`sql.Tx` 的 `ExecContext`。老 API 没有 ctx 版本的（比如某些第三方 SDK），只能靠额外起一个 goroutine + channel 把它包成可取消的形式：

```go
func callWithCtx(ctx context.Context, fn func() (T, error)) (T, error) {
    type result struct { v T; err error }
    ch := make(chan result, 1)              // 带缓冲，避免 goroutine 泄漏
    go func() { v, err := fn(); ch <- result{v, err} }()
    select {
    case r := <-ch:
        return r.v, r.err
    case <-ctx.Done():
        return zero, ctx.Err()              // 注意：底层调用并没有真被中断
    }
}
```

这段代码有个诚实的缺陷：**上层返回了，底层那个 goroutine 还在跑**，直到它自己结束。channel 带缓冲是为了让它能写进去然后退出。如果底层调用可能永久阻塞，这个方案也救不了你——那只能换掉那个 API。

## errgroup：并发任务里取消的传播

`errgroup` 有两个容易搞混的用法：

```go
g, ctx := errgroup.WithContext(parent)

for _, id := range ids {
    id := id
    g.Go(func() error {
        return fetch(ctx, id)     // 用 g 给你的 ctx，不是 parent
    })
}
if err := g.Wait(); err != nil { ... }
```

关键点：`errgroup.WithContext(parent)` 返回的 ctx **只有在一个任务返回非 nil error 时才会被 cancel**。所以：

- 一个任务失败 → 其他正在跑的任务收到取消信号 → `Wait()` 返回第一个错误
- 如果某个任务**不检查 ctx**，它不会因为别人失败而停下来，`Wait()` 会一直等它
- 第一个返回错误决定 `Wait()` 的返回值，其他错误被丢弃

如果你想「所有任务都跑完再汇总错误」，别用 errgroup 的取消语义，自己开 WaitGroup 收集错误（Go 1.20+ 可以配 `errors.Join`）。

另一个细节：**必须用 `g.Go` 里拿到的 `ctx`**。写成外层变量 `ctx`（shadows）看起来一样，实际拿的是没绑定的那个，取消不会生效。

## 超时的层级关系

嵌套 `WithTimeout` 时，**短的赢**：

```go
ctx1, _ := context.WithTimeout(context.Background(), 10*time.Second)
ctx2, _ := context.WithTimeout(ctx1, 3*time.Second)   // 实际 3s 就超时
```

这带来一个实践规则：**每一层只设自己的预算，不要设一个全局的固定值**。比如网关总预算 3s，调用服务 A 给 1s，A 内部调 B 给 500ms。如果每层都写死 10s，最上层的超时根本等不到，用户早就断了。

## 小结

- `context.Background()` / `TODO()` 会断掉父子链，派生 ctx 一定要基于上游 ctx
- 中间层的阻塞循环要自己 `select { case <-ctx.Done(): ... default: }`
- `select` 里同时放 `ctx.Done()` 和 `time.After`，别让计时器绕开 ctx
- 阻塞调用要用带 ctx 的 API；没有 ctx 版本的只能包 goroutine，并接受「底层不会真被中断」
- `errgroup` 的 ctx 只在**有任务返回错误时**取消，且第一个错误决定返回值
- 超时按层级分配预算，短的那个赢

一句话：`context` 传递**取消能力**，它不负责让代码停下来。每一层都得自己抬一下头看看信号。
