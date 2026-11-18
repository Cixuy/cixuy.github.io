---
title: epoll 的 LT 与 ET：一次漏读怎么把连接挂死
date: 2026-11-23
summary: 水平触发和边缘触发的判定条件、ET 为什么必须配非阻塞且循环读到 EAGAIN、EPOLLOUT 什么时候该注册——以及「连接还活着但再也不回包」是怎么来的。
tags: epoll, Linux, 网络编程, C++
---

写 Reactor 的时候，`epoll_ctl` 的最后一个参数决定了整个 IO 循环的写法。选错了不会编译报错，只会在压测时表现为「连接不报错、就是不回数据」。

## 两种触发模式的定义

`epoll_wait` 返回的是一个事件列表，同一个 fd 会不会反复出现，取决于注册时的模式：

| 模式 | 条件 | 行为 |
| --- | --- | --- |
| **LT**（水平触发，默认） | 缓冲区**还有数据**就通知 | 只要没读完，每次 `epoll_wait` 都会返回 |
| **ET**（边缘触发） | 状态**发生变化**才通知 | 一次可读只通知一次，读完不读完是你的事 |

换句话说：LT 是「有货就喊你」，ET 是「来货了喊你一次，之后不再提醒」。

## ET 的三条铁律

### 一、fd 必须是非阻塞

ET 下必须循环读到没有数据为止，而「读到没有」的标志是 `read` 返回 `EAGAIN`。如果 fd 是阻塞的，最后一次 `read` 会永远卡住——因为对端没关连接，内核也没数据了，`read` 就一直等。

```c
// 注册前一定要设非阻塞
int flags = fcntl(fd, F_GETFL, 0);
fcntl(fd, F_SETFL, flags | O_NONBLOCK);
```

### 二、必须循环读到 EAGAIN

```c
while (true) {
    ssize_t n = read(fd, buf, sizeof(buf));
    if (n > 0) {
        append_to_buffer(buf, n);   // 收到数据
    } else if (n == 0) {
        close_conn(fd);             // 对端正常关闭
        break;
    } else {
        if (errno == EAGAIN || errno == EWOULDBLOCK) break;  // 读干净了，正常退出
        if (errno == EINTR) continue;                        // 被信号打断，重试
        close_conn(fd);             // 真错误
        break;
    }
}
```

只读一次就返回，剩下的数据留在内核缓冲区里。ET 不会再通知你，这条连接就「假死」了：TCP 连接还在、`epoll_wait` 也不报错，但业务层再也收不到后续数据。这是 ET 最容易踩的坑，而且**本地测试很难复现**——数据包小的时候一次就读完了。

### 三、accept 也要循环

监听 fd 的 ET 同理：

```c
while (true) {
    int cfd = accept4(lfd, ..., SOCK_NONBLOCK);
    if (cfd < 0) {
        if (errno == EAGAIN) break;   // 连接队列空了
        if (errno == EINTR) continue;
        break;
    }
    register_read(cfd, EPOLLIN | EPOLLET);
}
```

一次只 accept 一个，剩下的连接堆在 backlog 里等下一次「状态变化」——但 ET 不会再通知，于是新连接全部超时。

## EPOLLOUT 的陷阱

`EPOLLOUT`（可写）在 ET 和 LT 下都容易出事，因为**socket 大部分时间都是可写的**。

- **LT + 一直注册 EPOLLOUT**：`epoll_wait` 几乎每次都立刻返回可写，CPU 直接跑满
- **ET + 一直注册**：同样是每次状态变化都通知，也会空转

正确做法是**按需注册**：

```
想发数据时先直接 write()
  ├─ 写完了 → 完全不碰 EPOLLOUT
  └─ 返回 EAGAIN（发送缓冲区满）→ 把剩余数据挂到连接的待发队列，
                                 这时才 epoll_ctl(MOD, EPOLLOUT)
等 EPOLLOUT 触发 → 继续写待发队列
  └─ 队列空了 → epoll_ctl(MOD, 去掉 EPOLLOUT)
```

判断标准很简单：**只有写不进去的时候才关心「可写」**。

## LT 也不是随便写就对

LT 的好处是简单：忘读了下次还会提醒，不容易假死。但有两个代价：

1. **多一次系统调用**：每次事件都要重新 `epoll_wait`
2. **容易空转**：如果只注册了 EPOLLIN 却一直不读（比如业务层限流），LT 会不停地返回可读，CPU 空转

所以 LT 的写法同样需要「读到 EAGAIN 为止」，只是**漏读的后果没那么严重**——下一次还会提醒你。

一个常见的折中：**LT + 非阻塞 + 每次尽量读完**。这样既不会假死，也不会因为一次没读完就永久卡住。

## 怎么判断线上是哪种问题

现象对比：

| 现象 | 大概率原因 |
| --- | --- |
| 连接不断、但不回包，`ss` 看 `Recv-Q` 有数据 | ET 漏读（内核缓冲区有货，应用没读） |
| CPU 单核 100%，`epoll_wait` 频繁返回 | LT 下注册了 EPOLLOUT 却没什么可写，或可读但没消费 |
| 新连接大量超时，但服务端 CPU 不高 | ET 下 accept 没循环，backlog 里堆着 |
| 压测 QPS 上不去，单连接延迟稳定 | 每次只读一个包，系统调用太多 |

`ss -tn` 里 `Recv-Q` 持续不为 0 是 ET 漏读的典型指纹——数据已经到了应用层，只是没人把它读走。

## 小结

- **LT**：有数据就通知，漏读下次还提醒；实现简单但可能空转
- **ET**：状态变化才通知；必须**非阻塞 + 循环读到 EAGAIN**，accept 也要循环
- **EPOLLOUT 按需注册**：只有 `write` 返回 EAGAIN 时才关心可写
- 选型建议：不确定就先 LT（+非阻塞+读干净），确认瓶颈在系统调用次数再换 ET

ET 的性能优势在于减少 `epoll_wait` 的重复返回，量级上是「少几次系统调用」；付出的代价是每条读写路径都必须写对循环。**用 ET 换来的性能，往往抵不上一次漏读造成的线上故障**——除非你已经把边界写成了肌肉记忆。
