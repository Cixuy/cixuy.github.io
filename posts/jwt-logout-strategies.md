---
title: JWT 无状态认证的登出方案对比：黑名单、短令牌与双 Token
date: 2026-11-17
summary: JWT 最大的卖点是"服务端不存状态"，所以"登出"这件事天生就别扭。四种方案的实现方式、代价和适用场景，以及我最后为什么选了双 Token。
tags: JWT, 认证, Spring Security
series: 认证与权限
---

面试里问 JWT，八成人能说出"无状态、不需要服务端存 session"；接着问一句"那用户点登出怎么办"，能答清楚的不多。

这不是刁难 —— 这是 JWT 设计上真实存在的矛盾：**令牌一旦签发，在过期之前就是有效的，服务端没有"作废"这个动作**。

我在智慧工地的权限模块里把四种方案都想过一遍，记录一下。

## 方案一：前端删掉 token

最省事：登出就是前端把 localStorage 里的 token 清掉，服务端什么都不做。

问题很明显 —— token 还在有效期内，任何人拿到它都能继续用。如果员工离职前复制了自己的 token，登出对他毫无约束。

**只适合一种场景：token 有效期极短（5 分钟内），且业务对"立即失效"没有要求。**

## 方案二：黑名单

签发时给每个 token 一个唯一 ID（JWT 标准里的 `jti`），登出时把这个 `jti` 写进 Redis，过期时间设为 token 剩余的有效期。每次校验时先查黑名单。

```java
// 登出
String jti = claims.getId();
long ttl = claims.getExpiration().getTime() - System.currentTimeMillis();
redis.opsForValue().set("jwt:blacklist:" + jti, "1", ttl, TimeUnit.MILLISECONDS);
```

```java
// 校验时
if (Boolean.TRUE.equals(redis.hasKey("jwt:blacklist:" + jti))) {
    throw new TokenInvalidException();
}
```

优点：能精确到单个 token，多设备互不影响。
缺点：**每个请求都要查一次 Redis**，"无状态"实际上变成了"有状态"，只是状态从内存挪到了 Redis。高频接口上这是一笔真实开销（虽然本机 Redis 是亚毫秒级）。

还有个容易漏的点：**`ttl` 必须按 token 剩余时间设置**，设成固定值（比如 30 分钟）会导致 token 过期了黑名单还占着内存，或者反过来黑名单先过期、token 还能用。

## 方案三：token 版本号

用户表加一个 `token_version` 字段，签发时把当前版本写进 claims，校验时比对。登出或者改密码就把版本 +1。

```java
// 校验时
Integer current = userCache.getTokenVersion(userId);
if (!claims.get("ver", Integer.class).equals(current)) {
    throw new TokenInvalidException();
}
```

优点：一次对比，不用存每个 token。
缺点：**这是"全员登出"** —— 用户在手机上登出，电脑上的登录也一起失效了。除非你的业务能接受（比如改密码就应该全踢），否则体验很差。

## 方案四：双 Token

`access_token` 有效期短（我设的 30 分钟），`refresh_token` 有效期长（7 天）且**存在服务端**（Redis 或 MySQL）。

- 登出：删掉 refresh_token 记录。access_token 最多再活 30 分钟。
- 刷新：拿 refresh_token 换新的 access_token。

优点：兼顾了性能和安全。大部分请求只校验 access_token 的签名，不查库；真正需要"作废"的 refresh_token 是存在服务端的。
缺点：实现复杂，要处理刷新时机、并发刷新、refresh 被重放。

## 对比

| 方案 | 登出是否立即生效 | 每请求开销 | 实现复杂度 | 适用 |
|---|---|---|---|---|
| 前端删除 | 否 | 无 | 极低 | token 极短、无强制下线需求 |
| 黑名单 | 是（精确到设备） | 查 Redis | 低 | 需要单设备登出 |
| 版本号 | 是（全员） | 查缓存 | 低 | 改密码全踢、简单系统 |
| 双 Token | access 不立即，refresh 立即 | 无（除刷新） | 高 | 有设备管理需求的产品 |

## 我为什么选双 Token

因为智慧工地那个系统有"设备管理"的需求 —— 用户要能在手机上看"我当前登录了哪几台设备，逐台踢掉"。这个功能只有 refresh_token 存服务端才做得出来，黑名单方案做不到（黑名单只知道 token 无效，不知道是哪个设备）。

如果没这个需求，我会选**方案二**，代码量不到双 Token 的三分之一。

## 实现时的几个坑

**1. `jti` 一定要有。** 不填 `jti` 的 JWT 在黑名单方案里根本没法定位。用 `UUID.randomUUID().toString()` 就行，别用时间戳（并发下会重复）。

**2. 刷新接口要防重放。** refresh_token 用一次就换一个新的（rotation），旧的立刻作废。如果同一个 refresh_token 被用了两次，说明可能泄露了，直接把该用户所有 refresh_token 清掉。

**3. 别把权限塞进 token。** 我第一版把角色列表写进了 claims，结果管理员改了某人的角色，得等 token 过期才生效。权限应该每次从缓存里取，token 里只放 `userId` 和 `jti`。

**4. 并发刷新。** 前端多个请求同时发现 access_token 过期，会并发触发刷新，把 refresh_token 用掉好几次。解决办法是前端加锁（只允许一个刷新请求在飞），或者后端允许多次刷新但只有一个成功。

## 小结

JWT 不是"不用管状态"，而是**把状态从服务端的内存挪到了客户端，并且用签名保证它没被改过**。一旦你有"作废"的需求，状态就得回来 —— 无非是决定它放在哪、代价是多少。

所以选方案之前先回答一个问题：**你的业务需要"立即踢下线"吗？** 需要 → 黑名单或双 Token；不需要 → 老老实实用短有效期，登出就是前端删 token，别为了技术感把系统搞复杂。
