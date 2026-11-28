---
title: 启动就报 Bean 冲突？Spring Boot 自动配置没生效的四种情况
date: 2026-11-30
summary: 自定义的 Bean 没被用上、@Conditional 明明写了却不生效、启动报 Bean 重复定义——按自动配置的执行顺序找出到底哪一步被覆盖了。
tags: Spring Boot, 自动配置, 排查, Java
---

「我明明配置了，为什么启动用的还是默认值」——这类问题八成不是配置写错了，而是**你的 Bean 和自动配置的 Bean 打架，而且输的一方跟你想的不一样**。

## 先搞清楚自动配置的执行顺序

Spring Boot 启动时，配置类的处理顺序大致是：

```
用户自己的 @Configuration / @Component
   ↓
自动配置类（@EnableAutoConfiguration 导入的那批）
   ↓
@Conditional 条件评估，不满足的直接跳过
   ↓
Bean 注册，后注册的覆盖先注册的（默认 allow-bean-definition-overriding=false）
```

关键点是最后一句：**用户配置先注册，自动配置后发现同名 Bean 会「退让」**。自动配置类上的 `@ConditionalOnMissingBean` 就是为了这个——你定义了就跳过它，没定义才用默认的。

所以「我的 Bean 没生效」只有两种可能：要么它压根没注册，要么它先被别人的注册顶掉了。

## 情况一：`@ConditionalOnMissingBean` 判断的是顺序，不是「有没有」

这是最反直觉的一条。看这段代码：

```java
@Configuration
public class AutoConfig {
    @Bean
    @ConditionalOnMissingBean
    public DataSource dataSource() { return new HikariDataSource(); }
}

@Configuration
public class MyConfig {
    @Bean
    public DataSource dataSource() { return new MyDataSource(); }
}
```

如果 `MyConfig` 先被处理，没问题。但如果 `MyConfig` 是被某个自动配置、或者 `@Import` 在更晚的阶段引入的，评估 `@ConditionalOnMissingBean` 时 `MyConfig` 还没注册，自动配置就认为「没有」，于是把默认 Bean 注册了。随后 `MyConfig` 再来注册同名 Bean，直接抛 `BeanDefinitionOverrideException`。

**`@ConditionalOnMissingBean` 只对「已经处理过的」Bean 生效**，它对处理顺序敏感。这就是为什么官方文档反复强调：自定义 `@ConditionalOnMissingBean` 的自动配置不要放在 `@AutoConfigureAfter` 之类靠后的位置。

判断方法：启动时加 `--debug`，日志里会打印 `ConditionEvaluationReport`，能看到每个条件当时的评估结果：

```
MyConfiguration matched (OnBeanCondition: ...)
 AutoConfig#dataSource: did not match (OnBeanCondition: @ConditionalOnMissingBean found beans: myDataSource)
```

## 情况二：`@ConditionalOnProperty` 大小写和前缀写错

```java
@ConditionalOnProperty(prefix = "app.cache", name = "enabled", havingValue = "true")
```

配置文件写的是：

```yaml
app:
  cache:
    Enabled: true      # ❌ 首字母大写
```

Spring 的 relaxed binding 对前缀和属性名是宽松的（`app.cache.enabled` / `APP_CACHE_ENABLED` / `app.cache.enabled` 都能对上），**但对 `havingValue` 的比较是严格字符串比较**。更常见的坑是：

```yaml
app:
  cache:
    enabled: yes        # ❌ havingValue = "true" 不匹配 "yes"
```

`@ConfigurationProperties` 绑定时 `yes` 能转成 `Boolean.TRUE`，但条件评估拿到的是原始字符串 `"yes"`，直接和 `"true"` 比，不相等。要么写 `true`，要么把 `havingValue` 改成 `"yes"`，或者干脆用 `havingValue = "true", matchIfMissing = false` 显式约定。

另一个坑：`@ConditionalOnProperty` 不写 `havingValue` 时，只要属性**存在**就匹配，值为 `false` 也匹配。很多人以为它会判断真假：

```java
@ConditionalOnProperty(name = "app.cache.enabled")   // enabled=false 也会生效！
```

## 情况三：`@ConditionalOnClass` 的类真的不在 classpath 上

```java
@ConditionalOnClass(RedisTemplate.class)
```

报错 `ClassNotFoundException` 或者自动配置完全没反应时，先确认依赖真的进来了：

```bash
mvn dependency:tree -Dincludes=org.springframework.data:spring-data-redis
```

常见原因：

- **`provided` / `test` scope**：编译期在，运行期不在，条件不满足
- **`optional=true` 的传递依赖**：直接依赖方拿不到
- **依赖被 exclusion 掉**：某个 starter 里排除了它，而你以为是全的

顺带一提，`@ConditionalOnClass` 写在 `@Bean` 方法上时，方法返回值类型里不能直接引用那个可选类（JVM 加载类时会解析返回值类型），否则类不存在时连加载都失败。正确写法是返回值声明成上层接口。

## 情况四：配置文件优先级搞混

Spring Boot 的配置优先级（从高到低，节选）：

```
命令行参数 --app.cache.enabled=false
   ↓
SPRING_APPLICATION_JSON
   ↓
application-{profile}.yml（jar 外部目录）
   ↓
application.yml（classpath）
   ↓
...
```

「我改了 `application.yml` 怎么不生效」的两个高频原因：

1. **profile 文件覆盖了它**：`application-prod.yml` 里有同名 key，优先级更高
2. **外部文件覆盖了它**：`config/application.yml` 或 `./application.yml` 存在，优先级高于 classpath

排查最直接的办法是启动后打印实际生效的值：

```java
@Bean
public ApplicationRunner dump(Environment env) {
    return args -> {
        System.out.println(env.getProperty("app.cache.enabled"));
        // 想更彻底就遍历所有 PropertySource 看同一个 key 被谁定义了
    };
}
```

还有一条容易被忽略：**`@Value` 在构造器里拿不到值**。它是在 Bean 实例化后由 `AutowiredAnnotationBeanPostProcessor` 注入的，构造阶段还是 `null`。要么用构造器参数注入（Spring 4.3+ 支持单构造器自动注入），要么用 `@ConfigurationProperties`。

## 一个快速排错的顺序

按这个顺序走，基本能定位到具体是哪一层：

| 步骤 | 命令 / 动作 | 看什么 |
| --- | --- | --- |
| 1 | 启动加 `--debug` | `ConditionEvaluationReport` 里目标配置是 matched 还是 missed |
| 2 | `mvn dependency:tree` | 条件依赖的类到底在不在 |
| 3 | 打印 `Environment.getProperty(key)` | 值是不是你以为的那个 |
| 4 | 打印所有同类型 Bean 名字 | 是不是已经有两个了，谁赢的 |

第 4 步用一段小代码就够了：

```java
@Component
public class BeanDumper implements ApplicationRunner {
    private final ApplicationContext ctx;
    public BeanDumper(ApplicationContext ctx) { this.ctx = ctx; }
    @Override
    public void run(ApplicationArguments args) {
        for (String n : ctx.getBeanNamesForType(DataSource.class)) {
            System.out.println(n + " -> " + ctx.getBean(n).getClass().getName());
        }
    }
}
```

## 小结

- 自动配置靠 `@ConditionalOnMissingBean` **退让**，而它对处理顺序敏感，不是「全局有没有」
- `@ConditionalOnProperty` 的 `havingValue` 是严格字符串比较，`yes`/`1` 都不等于 `true`；不写 `havingValue` 时只看存在性
- `@ConditionalOnClass` 的条件不满足，通常是依赖 scope / optional / exclusion 的问题
- 配置不生效先查 profile 和外部文件覆盖，`--debug` 的 ConditionEvaluationReport 是最快的入口
- `@Value` 在构造器里是 `null`，构造期取值要用构造器注入或 `@ConfigurationProperties`

判断一个「Bean 没生效」的问题，先问一句：**它是没被注册，还是被注册了但没赢？** 这两个方向查的东西完全不同。
