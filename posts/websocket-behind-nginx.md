---
title: WebSocket 部署到 Nginx 后连接断开？三个原因与完整配置
date: 2026-11-14
summary: 本地正常、上线就失败的典型场景：握手 400、连上 60 秒断、https 下直接连不上。逐条给出原因、配置和排查顺序。
tags: WebSocket, Nginx, 部署
---

智慧工地那个项目里，大屏需要实时显示塔吊的运行参数和告警截图，我把 HTTP 轮询换成了 WebSocket。本地开发一切正常，部署到测试服务器（前面挂 Nginx 做反向代理）之后，连接表现得很奇怪：有时候握手直接 400，有时候连上了但一分钟左右就断，前端只能靠重连硬扛。

后来把这三个坑一个个挖出来，记录一下。

## 坑一：`Upgrade` 头没有透传，握手直接 400

WebSocket 的握手本质上是一个带 `Upgrade: websocket` 头的 HTTP 请求。Nginx 默认按普通 HTTP 请求处理，不会把这个头往后传，后端拿到的就是一个普通 GET，握手自然失败。

第一版配置我写成了这样：

```nginx
location /ws {
    proxy_pass http://127.0.0.1:8080;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
}
```

能连上，但站点上其他普通 HTTP 接口全变成了长连接语义，`keep-alive` 行为被破坏。正确写法是用 `map` 判断来源请求到底是不是升级请求：

```nginx
map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    location /ws {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host       $host;
    }
}
```

两个容易漏的点：**`proxy_http_version` 必须是 1.1**（默认是 1.0，不支持升级）；`Connection` 不能硬编码成 `upgrade`。

## 坑二：`proxy_read_timeout` 默认 60 秒

这个坑最隐蔽 —— 连接建立成功了，握手也返回了 `101 Switching Protocols`，但**一分钟左右稳定断开**。

原因是 Nginx 的 `proxy_read_timeout` 默认 60s：如果后端在这个时间内没有向代理发过任何数据，Nginx 就认为连接空闲并关掉它。塔吊参数不是每秒都在变，业务上确实可能几十秒没有消息，于是连接就被判定为空闲。

两层都要处理：

```nginx
location /ws {
    proxy_read_timeout  300s;
    proxy_send_timeout  300s;
}
```

同时在应用层加心跳，比如前端每 25 秒发一次 ping，后端回 pong。**只加心跳不改 timeout 是不够的**，因为心跳间隔一旦大于 timeout，连接照样会被掐掉；反过来只调 timeout 不加心跳，中间网络设备（负载均衡、防火墙）也会静默丢弃长时间无流量的连接。

## 坑三：页面是 https，连接还在用 ws://

这个属于前端的低级错误，但现象很迷惑：浏览器控制台只报连接失败，看不出原因。

https 页面里发起 `ws://` 请求会被当作混合内容直接拦截。正确做法是按当前协议动态选：

```js
const proto = location.protocol === 'https:' ? 'wss://' : 'ws://';
const socket = new WebSocket(proto + location.host + '/ws');
```

如果是跨域部署，还要注意后端允许的 `Origin` 白名单。

## 我的排查顺序

踩完之后总结了一套固定顺序，比瞎猜快很多：

1. **先看握手响应码**。`400` → 头没透传，查 `Upgrade`/`Connection`；`404` → 路径或 `context-path` 不对；`502` → 后端没起来或代理地址错。
2. **`101` 但立刻断** → 看 Nginx 的 `error.log`，通常是 `upstream prematurely closed` 或超时。先用 `curl -i` 手动发一个带升级头的请求，确认后端行为。
3. **稳定 60 秒断** → 几乎可以断定是 `proxy_read_timeout`，配合心跳一起改。
4. **浏览器直接报错、请求都没发出去** → 查混合内容和 `Origin`。

另外一个经验：**服务端维护会话时一定要按业务维度分组**（我是按设备 ID），否则广播的时候遍历所有 session 既慢又容易把已经断开的会话算进去。断线重连之后要先推一份最新快照，不然大屏会一直停在旧数据上，看起来像"系统卡住了"。

---

这套配置现在跑在我自己的服务器上，坑是踩过了，但换个环境（比如上云之后前面多一层 SLB）大概还会遇到新问题。到时候再补。
