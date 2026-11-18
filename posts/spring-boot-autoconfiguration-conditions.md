---
title: Spring Boot 自动配置：@Conditional 在启动时怎么决定装哪些 Bean
date: 2026-11-20
summary: 从 @SpringBootApplication 的三件套，到 AutoConfiguration.imports 的加载顺序，再到 @ConditionalOnMissingBean 为什么有时不生效——把自动配置的装配过程拆开看。
tags: Spring Boot, 自动配置, Bean, Java
---

引入 `spring-boot-starter-web`，不用写一行配置就能跑起一个 Tomcat；自己写个 `DataSource` Bean 又不想要自动配置的那个——这些「自动」背后是一套条件装配机制。不理解它，遇到「我的 Bean 没生效」「两个 Bean 冲突了」就只能靠试。

## @SpringBootApplication 是三个注解的合体

```java
@SpringBootConfiguration      // 本质是 @Configuration
@EnableAutoConfiguration      // 关键：开启自动配置
@ComponentScan                // 扫当前包及子包
public @interface SpringBootApplication {}
```

`@ComponentScan` 只负责扫你自己写的组件，`@EnableAutoConfiguration` 才是「把别人 jar 里的配置也装进来」的开关。它通过 `@Import(AutoConfigurationImportSelector.class)` 触发。

## 自动配置类是从哪读出来的

`AutoConfigurationImportSelector` 做的事就一件：找出所有候选自动配置类，然后按条件筛。

- **Spring Boot 2.x**：读所有 jar 里的 `META-INF/spring.factories`，取 `EnableAutoConfiguration` 这个 key
- **Spring Boot 3.x**：改读 `META-INF/spring/org.springframework.boot.autoconfigure.AutoConfiguration.imports`，一行一个类名

文件内容长这样（以 MyBatis 起步依赖为例）：

```text
org.mybatis.spring.boot.autoconfigure.MybatisAutoConfiguration
org.mybatis.spring.boot.autoconfigure.MybatisLanguageDriverAutoConfiguration
```

注意这里只是**候选清单**。清单上的类不是全都会被注册，每个类上面都挂着一串条件注解，条件不满足就整个跳过——而且是**静默跳过**，不报错。这就是为什么「加了依赖但没生效」时日志里什么都看不到。

## 条件注解的家族

| 注解 | 判断什么 | 典型用法 |
| --- | --- | --- |
| `@ConditionalOnClass` | 类路径上有没有某个类 | 有 `DataSource` 才装配数据源 |
| `@ConditionalOnMissingBean` | 容器里**还没有**某类型的 Bean | 让你自己的 Bean 覆盖默认实现 |
| `@ConditionalOnProperty` | 配置项是否为某个值 | `spring.datasource.enabled=true` 才开 |
| `@ConditionalOnWebApplication` | 是不是 Web 环境 | Servlet 相关配置只在 Web 应用生效 |
| `@ConditionalOnBean` | 容器里**已经有**某个 Bean | 依赖别的配置先装配 |

前两个最常用，也最容易混：`OnClass` 看的是 classpath（编译期），`OnBean` 看的是容器（运行期，而且有顺序依赖）。

## 装配顺序：为什么你的 Bean 能覆盖默认的

`@ConditionalOnMissingBean` 能不能生效，取决于**用户配置类和自动配置类谁先被处理**。

Spring Boot 的做法是把自动配置类当作 **deferred import**（延迟导入），在所有用户自己的 `@Configuration` 处理完之后再解析。于是顺序是：

```
用户配置类（@ComponentScan 扫到的、你自己写的） → 自动配置类
```

所以你在自己类里定义的 `DataSource` 先注册进容器，自动配置那个 `@ConditionalOnMissingBean(DataSource.class)` 一看已经有了，就跳过。这就是「自定义 Bean 能覆盖默认实现」的原理——**靠的不是优先级高，而是处理得早**。

反过来说，如果两个都是自动配置，就要显式声明相对顺序：

```java
@AutoConfiguration(before = DataSourceAutoConfiguration.class)
@AutoConfigureAfter(MybatisAutoConfiguration.class)
```

`@AutoConfigureOrder` 也能用，但数字只对同批次生效，不如 `before/after` 直观。

## 排查「为什么没生效」

Spring Boot 内置了条件评估报告，两种打开方式：

```bash
java -jar app.jar --debug
# 或者在 application.yml 里
logging:
  level:
    org.springframework.boot.autoconfigure: DEBUG
```

输出里会有三段，最有用的是 **Negative matches**：

```text
Negative matches:
---------------
   DataSourceAutoConfiguration:
      Did not match:
         - @ConditionalOnClass did not find required class 'javax.sql.DataSource'
```

这段直接告诉你「这个配置被跳过了，因为哪个条件不满足」，比猜快得多。

## 三个常见坑

**一、`@ConditionalOnClass` 写错类名，编译不报错但永远不生效。**
注解里的 `name` 是字符串时不会做编译期校验，类名写错就静默失败。能用 `value = Xxx.class` 就别用 `name = "..."`。

**二、自定义配置类写在启动类所在包的**外面**，`@ComponentScan` 扫不到。**
自动配置和你自己的配置都失效时，先确认包路径。启动类默认扫它所在包及子包。

**三、`@ConditionalOnProperty` 配了 `havingValue` 但类型对不上。**
`havingValue = "true"` 只匹配字符串 `true`，配成 `TRUE` 或 `1` 都不算。要么统一写小写 `true`，要么用 `matchIfMissing` 显式表达默认行为。

## 小结

自动配置不是「魔法」，就三步：

1. **收集**：从所有 jar 的 `AutoConfiguration.imports`（3.x）里读出候选类
2. **排序**：按 `@AutoConfigureBefore/After` 排出相对顺序
3. **筛选**：逐个评估条件注解，不满足就跳过；因为自动配置是延迟导入，用户自己的 Bean 总是先注册，`@ConditionalOnMissingBean` 才能实现「覆盖默认」

搞不清某个配置为什么在/不在，别猜，开 `--debug` 看条件评估报告——它把「为什么不匹配」写得很清楚。
