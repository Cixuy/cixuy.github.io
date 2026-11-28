---
title: ThreadLocal 用完没清理导致用户信息串号，问题出在线程池复用
date: 2026-11-29
summary: 偶发串号的根因不是并发写，而是线程复用：set 过的值被下一个请求读了。讲清 InheritableThreadLocal 为什么在池里失效、remove 应该放在哪、异步任务怎么传递。
tags: Java, ThreadLocal, 并发, 线程池
---

线上偶发「用户 A 看到用户 B 的数据」，刷新一下又正常。监控里没有任何异常，日志里两边的 traceId 也都对得上。最后查出来是 `ThreadLocal` 用错了——准确说，是**用完没清理**。

## 先说结论：串号的机制

`ThreadLocal` 的值挂在 `Thread` 对象自己身上（`ThreadLocalMap`），不是挂在请求上。Tomcat 的 worker 线程是**复用的**：

```
请求 A → worker-1 执行 → set(userA) → …… 请求结束
请求 B → worker-1 执行 → 如果没人 set，get() 拿到的还是 userA
```

所以问题不是「并发写得乱」，而是**上一个请求留下的残值被下一个请求读到了**。这也解释了为什么现象是「偶发」——只在同一个线程恰好连续处理两个请求、且第二个请求没覆盖该值时出现。

## 为什么会忘记清

常见的写法是「在拦截器里 set，业务里随便用」：

```java
public class AuthInterceptor implements HandlerInterceptor {
    @Override
    public boolean preHandle(HttpServletRequest req, HttpServletResponse resp, Object handler) {
        UserContext.set(parseToken(req));
        return true;
    }
    // 没有 afterCompletion
}
```

看起来没问题：每个进来的请求都会重新 set 一次，为什么不覆盖？

因为存在**不走拦截器的路径**：静态资源、`/health`、以及拦截器 `preHandle` 返回 `false` 之后的异常分支。更要命的是异步任务：

```java
UserContext.set(user);
executor.submit(() -> {
    // 这里是另一个线程，UserContext.get() 拿到的是这个线程上一次任务的值
    doSomething();
});
```

线程池里的线程有自己的 `ThreadLocalMap`，`executor.submit` **不会**把当前线程的值带过去。如果那个 worker 线程之前执行过别的任务、残留了值，这里就读到了别人的。

## 三个容易踩的变体

**1. `InheritableThreadLocal` 在线程池里失效。** 它是靠「创建线程时复制父线程的值」实现的，只在 `new Thread()` 那一刻生效。线程池的线程是提前创建好并复用的，创建时的父线程可能跟当前请求完全无关，所以值不会跟着任务走。要跨线程池传递，得用阿里 `TransmittableThreadLocal` 之类在提交任务时做快照的方案。

**2. `set(null)` 不等于 `remove()`。** `remove()` 会从 `ThreadLocalMap` 里删掉整个 Entry；`set(null)` 只是把 value 置空，Entry 还在，key 还是那个弱引用的 ThreadLocal。前者不占内存，后者留一条垃圾记录。更关键的是 `get()` 的语义：`remove` 后走 `initialValue()`，`set(null)` 后返回 `null`——如果你的 `initialValue()` 有默认值，这两者行为完全不同。

**3. 父子任务的上下文覆盖。** 主线程 `set` 了用户 A，提交异步任务时又 `set` 成系统用户，任务完成后没恢复，主线程后续逻辑就用上系统用户了。异步链路里 set/restore 必须成对。

## 正确写法：try-finally 是唯一可靠的位置

不要依赖拦截器「一定会执行」。把 set 和清理绑在同一个作用域里：

```java
public void handle(Request req) {
    UserContext.set(parse(req));       // 进入作用域先进
    try {
        business();                    // 只有这里能用
    } finally {
        UserContext.remove();          // 出去必清，异常也不漏
    }
}
```

拦截器场景则必须在 `afterCompletion` 里清，而且 `afterCompletion` 在 `preHandle` 返回 `false` 时**不会**被调用——所以别在 `preHandle` 里 set，改成在 `afterCompletion` 与业务入口两处成对处理，或者干脆用一个 `Filter`：

```java
public class ContextFilter extends OncePerRequestFilter {
    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse resp,
                                    FilterChain chain) throws IOException, ServletException {
        try {
            UserContext.set(parse(req));
            chain.doFilter(req, resp);
        } finally {
            UserContext.remove();      // Filter 的 finally 一定会执行
        }
    }
}
```

`Filter` 比 `Interceptor` 更适合干这件事：它在 `chain.doFilter` 的 `finally` 里，请求走哪条分支都会回来。

## 异步任务怎么正确传递

线程池提交任务时手动捕获、手动恢复：

```java
User u = UserContext.get();
String traceId = TraceContext.get();
executor.submit(() -> {
    UserContext.set(u);                // 主动带过去
    TraceContext.set(traceId);
    try {
        doSomething();
    } finally {
        UserContext.remove();          // 线程还要还给池子，必须清
        TraceContext.remove();
    }
});
```

嫌啰嗦就封装一个 `Executor` 装饰器，在 `execute`/`submit` 里做同样的事。原则只有一条：**在线程池里 set 的东西，必须在同一个 runnable 的 finally 里清掉**，否则这个线程回到池子时是脏的。

## 怎么快速定位这类问题

串号问题时在日志里打上线程名，观察两次出错请求是不是落在同一个 worker：

```
[tomcat-exec-7] user=customerA ...
[tomcat-exec-7] user=customerB ...
```

如果是同一个线程连续出现，基本就确认了。再补一条：在 `UserContext.get()` 里加一条「`get()` 拿到值但当前请求没有认证头」的告警日志，能提前抓出来。

## 小结

- 串号的根因是**线程复用 + 残值未清**，不是并发写冲突
- 清理位置只有 `try-finally` 可靠，`Filter` 的 `finally` 优于 `Interceptor` 的 `afterCompletion`
- `InheritableThreadLocal` 对线程池无效，跨线程要显式传递或快照
- `set(null)` ≠ `remove()`，前者留垃圾 Entry 且改变 `get()` 的默认值语义
- 异步任务里 set 了就要在 `finally` 里清，线程要还回池子

一句话：`ThreadLocal` 是「线程的」，不是「请求的」。把它当成请求作用域变量用，迟早出事。
