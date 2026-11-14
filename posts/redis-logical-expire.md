---
title: 缓存击穿实战：逻辑过期 + Redisson 分布式锁，以及我踩的四个坑
date: 2026-11-12
summary: 两种方案对比、逻辑过期的实现方式，以及重建互斥、锁续期、线程池隔离、失败退避这四个必须处理的细节。
tags: Redis, 缓存, 并发
series: 缓存与并发
updated: 2026-11-14
---

设备状态和权限字典在智慧工地项目里是被高频读的：大屏每几秒刷一次，每个接口都要查当前用户权限。这类数据变化不频繁，放进 Redis 很合适，问题是**热点 key 过期的那一刻**——如果几千个请求同时发现缓存没了，会一起打到 MySQL 上。

## 两种方案，先想清楚要什么

| | 互斥锁（SETNX） | 逻辑过期 |
|---|---|---|
| 一致性 | 强，一定返回最新值 | 弱，可能返回旧值 |
| 可用性 | 差，抢不到锁的请求要等待 | 好，永远立即返回 |
| 实现复杂度 | 低 | 中 |
| 适合场景 | 金额、库存等不能读旧值的 | 状态、配置、字典等能容忍短暂不一致的 |

我选的是逻辑过期：设备状态晚 100ms 更新，业务上完全无所谓，但接口不能被阻塞。

思路是 **value 里不放 TTL，而是放一个 `expireAt` 字段**，key 本身永不过期。读到数据后先判断 `expireAt`：

- 没过期 → 直接返回
- 已过期 → **先把旧值返回给调用方**，同时提交一个异步任务去重建缓存

## 坑一：异步重建没有互斥，DB 被打得更惨

第一版很天真：发现过期就 `executor.submit(() -> rebuild(key))`。结果是过期瞬间，100 个请求各自提交了重建任务，**100 个线程同时查同一个 DB** —— 比不用缓存还糟。

重建必须互斥，用 Redisson：

```java
public void rebuild(String key) {
    RLock lock = redissonClient.getLock("lock:" + key);
    // waitTime=0：拿不到锁直接返回，不排队
    // leaseTime 不传，交给看门狗自动续期
    if (!lock.tryLock(0, TimeUnit.SECONDS)) {
        return;
    }
    try {
        Object latest = loadFromDb(key);
        redis.opsForValue().set(key, new CacheWrapper(latest, System.currentTimeMillis() + TTL));
    } finally {
        if (lock.isHeldByCurrentThread()) {
            lock.unlock();
        }
    }
}
```

注意 `tryLock` 的第一个参数用 `0`：**抢不到锁就直接放弃**，因为旧值已经返回给用户了，这次重建失败不影响正确性，下一次请求还会再触发。

## 坑二：锁的租期小于重建耗时，锁提前释放

这是我第二版的问题。当时为了"避免死锁"，给锁显式设了 `leaseTime = 3s`。但重建逻辑里有一次全量查询，慢的时候要 5 秒 —— 锁在第 3 秒就被释放了，第二个线程进来又开一份重建，互斥形同虚设。

两种解法：

- **不传 `leaseTime`**，用 Redisson 的看门狗（默认 30 秒，每 10 秒续一次），业务没跑完锁就不会掉
- 或者显式给足时间，但必须大于重建耗时的上限 —— 而这个上限你往往估不准

我最后选了看门狗。代价是如果进程直接 kill -9，锁要等 30 秒才自然过期，不过对重建场景无所谓。

## 坑三：线程池用错了，异步任务反而拖垮主流程

一开始图省事用了 `CompletableFuture.runAsync(...)`，它跑在 `ForkJoinPool.commonPool()` 上 —— 这是整个 JVM 共享的池子，默认并行度只有 CPU 核数 - 1。压测时重建任务把 commonPool 占满，**其他异步逻辑（包括日志、通知）开始排队**。

改成专用线程池，并且队列满了要有明确的拒绝策略：

```java
ThreadPoolExecutor rebuildPool = new ThreadPoolExecutor(
    4, 8, 60L, TimeUnit.SECONDS,
    new ArrayBlockingQueue<>(200),
    new ThreadFactoryBuilder().setNameFormat("cache-rebuild-%d").build(),
    new ThreadPoolExecutor.DiscardPolicy()  // 丢弃 + 打日志，不要 CallerRuns
);
```

拒绝策略这里有个反直觉的点：**不要用 `CallerRunsPolicy`**。它会让提交任务的线程（也就是处理用户请求的 Tomcat 线程）自己去重建缓存，一个本该 10ms 返回的接口突然变成 500ms。丢弃 + 告警，让下一次请求重试，反而更稳。

## 坑四：重建失败后 `expireAt` 没更新，每秒都在打 DB

如果 `loadFromDb` 抛异常（DB 抖动、超时），`expireAt` 还是过期状态，下一个请求判定过期 → 再提交重建 → 再失败。**一个写坏的 key 会变成每秒一次的 DB 查询**，而且是无限循环。

修法是失败也要"续一段命"，做个退避：

```java
catch (Exception e) {
    log.error("cache rebuild failed, key={}", key, e);
    // 保留旧值，但把过期时间往后推 30 秒，避免密集重试
    redis.opsForValue().set(key, new CacheWrapper(oldValue, System.currentTimeMillis() + 30_000));
}
```

这一步不加，监控上看不出问题，但 DB 的 QPS 会莫名其妙多出一截 —— 我是在看慢查询日志时才发现有个 key 在反复被查。

## 顺带说雪崩

逻辑过期只解决"单个热点 key"的问题。如果大量 key 在同一时刻过期（比如服务重启后统一加载），还是会撞在一起。办法是**基础 TTL 加随机抖动**：

```java
long ttl = BASE_TTL + ThreadLocalRandom.current().nextLong(-BASE_TTL / 10, BASE_TTL / 10);
```

另外缓存整体不可用（Redis 挂了）时要有降级：直接查库 + 限流，别让请求全压到 MySQL 上把数据库一起带走。

## 小结

逻辑过期的核心不是"异步重建"这四个字，而是围绕它的一圈配套：**重建互斥、锁续期、线程池隔离、失败退避**。少任何一个，方案都会在压测或者线上某一刻失效。

至于具体数值，建议自己压一遍再决定 —— 我这套参数也是本地压测之后才定下来的，别人环境里的数字对你不一定有参考价值。
