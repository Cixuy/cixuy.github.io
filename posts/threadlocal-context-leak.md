---
title: ThreadLocal 用完没清理导致用户信息串号，问题出在线程池复用
date: 2026-11-15
summary: 两个账号同时操作，一个看到了另一个的数据。本地怎么都复现不了，根因是 Tomcat 线程池复用线程时上一次的 ThreadLocal 没被清掉。
tags: Java, 并发, ThreadLocal
series: 缓存与并发
---

这个问题是我在给权限模块加上下文透传之后出现的，表现非常吓人：测试同事用 A 账号登录，刷新列表页，有几行数据显示的是 B 账号的信息。

第一反应是 SQL 写错了，或者前端缓存了旧数据。查了半天都不是。最后是在日志里发现线索：**那条请求打印出来的 userId 是 B 的，但当时登录的是 A。**

## 为什么本地永远复现不了

因为本地开发时请求量小，每次请求大概率拿到一个新线程。而线上 Tomcat 的线程池里有几百个线程反复复用，只有"同一个线程上一次处理的请求没清理上下文"的时候才会出问题。

复现方法很简单：把 Tomcat 的最大线程数调到 1，然后连续发两个不同账号的请求。

```yaml
server:
  tomcat:
    threads:
      max: 1
```

第二个请求立刻会拿到第一个请求的 userId。这个实验很有价值 —— 它把"偶发"变成了"必现"，你才能真正确定根因。

## 根因

我当时的写法是这样：

```java
public class UserContext {
    private static final ThreadLocal<LoginUser> HOLDER = new ThreadLocal<>();

    public static void set(LoginUser user) { HOLDER.set(user); }
    public static LoginUser get() { return HOLDER.get(); }
}
```

拦截器里 `set`，业务里 `get`，但**没有任何地方 `remove`**。

Tomcat 的线程是复用的：请求结束后线程回到池里，`ThreadLocal` 的值还挂在线程的 `ThreadLocalMap` 上。下一个请求分到同一个线程，读到的就是上一个用户的身份。

这里有个容易误解的点：`ThreadLocal` 的 key 是弱引用，很多人以为"GC 会帮我清掉"。不会 —— 弱引用回收的是 key（ThreadLocal 对象本身），而 **value 是强引用，会一直活到线程结束**。所以这既是串号问题，也是内存泄漏问题。

## 修法

最小改动是在拦截器里配对清理：

```java
@Override
public void afterCompletion(HttpServletRequest req, HttpServletResponse resp,
                            Object handler, Exception ex) {
    UserContext.clear();   // 内部调用 HOLDER.remove()
}
```

但只加 `remove` 还不够稳 —— 只要有人写了个新拦截器、或者在异步链路里漏了一次，问题就会回来。所以我后来改成了统一入口：

```java
public final class UserContext {
    private static final ThreadLocal<LoginUser> HOLDER = new ThreadLocal<>();

    public static void run(LoginUser user, Runnable task) {
        HOLDER.set(user);
        try {
            task.run();
        } finally {
            HOLDER.remove();
        }
    }

    public static LoginUser get() { return HOLDER.get(); }
}
```

把 `set` 变成私有的，外部只能通过 `run` 使用，生命周期由这一个方法兜住。**能编译通过的写法就不容易忘。**

## 第二个坑：异步线程里拿不到

后面加异步任务（比如告警推送）时又踩了一次：在 `@Async` 方法里 `UserContext.get()` 返回 null。

原因是 `ThreadLocal` 是线程私有的，线程池里的线程跟提交任务的线程不是同一个。`InheritableThreadLocal` 也解决不了 —— 它只在线程**创建**时继承，而线程池的线程是早就创建好的。

三种办法：

| 方案 | 说明 |
|---|---|
| 显式传参 | 最土也最可靠，把 user 作为方法参数传进去 |
| `TransmittableThreadLocal` | 阿里开源的 TTL，配合它的线程池包装器使用 |
| 手动透传 | 提交任务前 `get` 出来，在任务里 `set`，用完 `remove` |

我选了显式传参 —— 异步任务里本来就不该依赖隐式的上下文，依赖了就会出一个很难查的 bug。

## 小结

`ThreadLocal` 的问题不在于用法，在于**生命周期没人管**。写的时候只要记住两句话：

1. **谁 set 谁 remove**，而且必须放在 `finally` 里
2. **跨线程不传值**，异步场景显式传参

最后一句经验：这类"偶发串号"的问题，只要能构造出多线程交替执行的环境，就一定能稳定复现。别在单线程的本地环境里跟自己较劲。
