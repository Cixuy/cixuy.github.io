---
title: 接 MCP 协议时踩的坑：Java SDK 和 Jackson 版本打架
date: 2026-11-06
summary: 编译通过、打包成功、启动一切正常，第一个 MCP 请求进来就 500。NoSuchFieldError: POJO 的完整排查过程，以及这个 SDK 的 JSON 层为什么是插件化的。
tags: MCP, Java, Jackson, Spring Boot
series: 从零做一个 AI Agent
chapter: 2
---

给代码评审服务加 MCP 支持，让外部 AI 客户端能把"评审代码"当成一个工具调用。用的是官方 Java SDK，`io.modelcontextprotocol.sdk:mcp:1.1.5`，加一行依赖就完事。

结果：编译通过，`mvn package` 成功，服务正常启动，SSE 端点连上也能收到 `endpoint` 事件。**但第一个 `initialize` 请求打进来，直接 500。**

## 现象

```text
POST /mcp/message -> HTTP 500
Servlet.service() for servlet [mcp-sse-transport] threw exception
```

日志往下翻，两行异常，都在 `Caused by` 里：

```text
java.lang.NoSuchFieldError: POJO
java.lang.NoSuchMethodError: 'com.fasterxml.jackson.annotation.OptBoolean
    com.fasterxml.jackson.annotation.JsonProperty.isRequired()'
```

`NoSuchFieldError` 和 `NoSuchMethodError` 这两个异常很特殊——它们不是"找不到类"，而是**类找到了、方法签名对不上**。典型的运行时版本冲突。

## 排查

先确认 SDK 到底依赖了什么：

```bash
mvn dependency:tree -Dincludes=com.fasterxml.jackson.core
```

输出里同时出现了两个 Jackson：

```text
com.fasterxml.jackson.core:jackson-annotations:2.15.4
tools.jackson.core:jackson-annotations:3.0.3
```

注意 `tools.jackson` 这个包名——**Jackson 3 换了 groupId 和包名**，从 `com.fasterxml.jackson` 变成了 `tools.jackson`。两套注解类名一模一样（都有 `@JsonProperty`），但字节码不兼容。

Spring Boot 3.2 用的是 Jackson 2，而 MCP SDK 的默认适配器 `mcp-json-jackson3` 绑的是 Jackson 3。`JsonProperty.isRequired()` 这个方法在 Jackson 2 的注解里根本不存在，运行时一反射就炸。

编译期完全看不出来，因为 SDK 自己的代码是按 Jackson 3 编译的，我只调 `McpServer.builder()` 这些 API，不直接碰注解。

## 解法

SDK 的 JSON 层是插件化的，`mcp-json-jackson3` 和 `mcp-json-jackson2` 是两个可互换的实现。把聚合包换成 core + jackson2 适配器：

```xml
<dependency>
    <groupId>io.modelcontextprotocol.sdk</groupId>
    <artifactId>mcp-core</artifactId>
    <version>1.1.5</version>
</dependency>
<dependency>
    <groupId>io.modelcontextprotocol.sdk</groupId>
    <artifactId>mcp-json-jackson2</artifactId>
    <version>1.1.5</version>
</dependency>
```

代码里对应的实现类也换一个包：

```java
// 之前：绑定 Jackson 3
// import io.modelcontextprotocol.json.jackson3.JacksonMcpJsonMapper;
// return new JacksonMcpJsonMapper(JsonMapper.builder().build());

// 之后：绑定已有的 Jackson 2
import io.modelcontextprotocol.json.jackson2.JacksonMcpJsonMapper;

@Bean
public McpJsonMapper mcpJsonMapper() {
    return new JacksonMcpJsonMapper(new ObjectMapper());
}
```

重新打包启动，`initialize` 返回正常，`tools/list` 列出三个工具。

## 为什么这个坑值得记

**第一，`NoSuchFieldError` / `NoSuchMethodError` 要优先怀疑依赖冲突，不是代码写错。** 这类异常的堆栈通常指向调用方，但根因在 classpath。看到它们先跑 `dependency:tree`。

**第二，同一个库的大版本可能换包名。** Jackson 2 到 3 换了 groupId 和根包名，这让"两个版本共存"变得很隐蔽——Maven 不会把它们当成同一个 artifact 的冲突，两个都能装上，编译期也不报错。

**第三**，接入带 JSON 序列化的 SDK 时，**先看它的 JSON 层绑的是哪个序列化库**。MCP SDK 这种设计是合理的（不强制用户用某个 JSON 库），但默认值不一定匹配你的框架版本。

---

顺带记一下：换依赖的时候 `mvn package` 会报 `Unable to rename ... .jar to ... .jar.original`。这不是配置问题，是**上一次启动的服务还占着那个 jar**。Windows 上文件锁是硬的，`taskkill` 掉进程再打包就行。
