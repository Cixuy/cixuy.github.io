---
title: Reactor 里 return 和 break 差之毫厘：客户端收不到任何响应
date: 2026-11-16
summary: 健康检查接口不报错、不超时、也不返回数据，就是没反应。根因是一个循环里的 return 跳过了后面的发送逻辑。
tags: C++, epoll, Reactor, HTTP
series: 系统编程
---

用 C++ 写了个小的 HTTP 服务，epoll + 非阻塞 IO，读了数据、解了请求、生成了响应。启动正常，端口能连上，但 `curl` 一直是空白——不报错，也不超时，就是没输出。

这种"什么都不发生"的现象最难查，因为没有错误信息。

## 现象

```bash
curl -v http://127.0.0.1:18080/health
```

输出停在握手成功之后：

```text
* Established connection to 127.0.0.1 (127.0.0.1 port 18080)
> GET /health HTTP/1.1
> Host: 127.0.0.1:18080
>
```

没有响应头，没有超时提示。连接建起来了，请求也发出去了，服务端什么都没返回。

对比几种失败形态会更容易定位：

| 现象 | 含义 |
|---|---|
| 卡住不动，直到超时 | 连上了但服务端不回数据 |
| `Connection refused` | 服务没启动 |
| 返回 4xx/5xx | 协议层正常，业务层出错 |
| 立刻返回空 | 返回了 200 但 body 为空 |

第一种说明 TCP 层没问题，问题在应用层——**请求处理完了，但响应没写回去**。

## 排查

先确认服务端到底收到请求没有，在 handler 里加日志：

```cpp
std::string Server::route(const Request& req) {
    printf("[route] %s %s\n", req.method.c_str(), req.path.c_str());
    ...
}
```

日志打出来了，说明请求解析正常、路由命中、响应体也生成了。

再看发送逻辑的调用点，也就是 `on_readable` 的结尾：

```cpp
// 一次读循环里可能包含多个流水线请求，逐个处理
for (;;) {
    Request req;
    int consumed = parse_request(in.data(), in.size(), req);
    if (consumed == 0) return;      // 数据不足
    ...
    out += build_response(200, "application/json", body);
}

// 发送逻辑在这里
poller_->modify(fd, Event::Readable | Event::Writable);
on_writable(fd);
```

问题就在那个 `return`。

## 根因

一次事件循环里，`for (;;)` 的语义是"把这个缓冲区里能解析的请求全部处理掉"。

- 第一次循环：解析成功，响应写进 `out` 缓冲区，`continue` 进入下一轮
- 第二次循环：缓冲区已经被 `erase` 空了，`parse_request` 返回 0 表示"数据不足"

这时候 `consumed == 0` 直接 `return`——**整个函数返回了，于是后面那三行发送逻辑一行都没执行**。

响应安安静静躺在 `out` 里，直到连接被客户端关掉，或者下一次有数据进来才可能被发出去。而 `curl` 发完一个请求就不动了，所以永远等不到。

`consumed == 0` 表示的是"这一轮解析结束"，不是"这个请求处理结束"。用 `return` 把两者混为一谈，等于把"发送"这本书撕掉了最后一页。

## 修复

```cpp
for (;;) {
    Request req;
    int consumed = parse_request(in.data(), in.size(), req);
    // 数据不足只代表本轮解析结束，不能直接返回，
    // 否则已排队的响应不会被发送出去（客户端会一直等）
    if (consumed == 0) break;
    ...
}
```

一行 `return` 改成 `break`，客户端立刻收到响应：

```text
< HTTP/1.1 200 OK
< Content-Type: application/json
< Content-Length: 40
< Connection: keep-alive
<
{"status":"ok","service":"code-insight"}
```

## 为什么这种 bug 特别难发现

**编译期零警告。** `return` 和 `break` 都是合法语句，如果函数返回值不是 `void`，编译器可能会提示"缺少返回值"，但这个函数正好是 `void`。

**没有异常。** 没有任何一层抛错，进程健康，CPU 空闲。

**静态检查也难发现。** 它不是内存泄漏、不是空指针、不是资源未释放，逻辑上"看起来"是合理的——数据不够就返回，很自然。

唯一的发现途径就是跑起来看一眼输出。这也是为什么这类系统程序写完必须真跑一次：编译器只能保证语法和类型，保证不了控制流语义。

## 顺带一个相关经验

同一个循环里，我原来还写了个小优化：每解析完一个请求就 `erase` 掉已消费的字节。这本身没错，但要注意 `erase` 之后的 `data()` 指针会变，**下一轮解析必须重新取指针，不能缓存上一轮拿到的 `char*`**。

```cpp
// 错的：buffer 的指针在 erase 之后失效
const char* p = in.data();
for (;;) { parse_request(p, ...); in.erase(0, n); }

// 对的：每轮重新取
for (;;) { parse_request(in.data(), in.size(), req); ... }
```

这里用的是 `std::string`，`erase` 可能导致重新分配。如果哪天换成自己管理的缓冲区，这个细节就是内存错误的来源。
