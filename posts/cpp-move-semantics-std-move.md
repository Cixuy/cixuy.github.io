---
title: C++ 移动语义：std::move 之后对象还剩什么
date: 2026-11-24
summary: std::move 只是类型转换、moved-from 对象的真实状态、RVO 何时绕过移动、为什么返回局部变量不用 std::move——四个最常被写错的点。
tags: C++, 移动语义, 右值引用, 性能
---

`std::move` 这个名字骗了很多人：它**不移动任何东西**，只是一个 `static_cast<T&&>`。真正发生资源转移的是移动构造函数，而是否被调用、调用后源对象变成什么样，有几条容易记错的规则。

## std::move 只是把左值转成右值

标准库里的实现大概长这样：

```cpp
template <typename T>
constexpr std::remove_reference_t<T>&& move(T&& t) noexcept {
    return static_cast<std::remove_reference_t<T>&&>(t);
}
```

它做的事只有一件：**把表达式强制转成右值引用**，从而让重载决议选中移动构造函数（或移动赋值）。没有内存拷贝，没有资源转移。

所以下面这段代码什么都没有发生：

```cpp
std::string s = "hello";
std::move(s);            // 只是构造了一个右值引用，然后丢弃
std::cout << s;          // 还是 hello
```

真正触发移动的是「用这个右值去构造/赋值」：

```cpp
std::string t = std::move(s);   // 这里才调用移动构造，s 的内容被搬走
```

## moved-from 对象是什么状态

标准规定：**valid but unspecified**（有效但未指定）。翻译成人话：

- ✅ 可以析构
- ✅ 可以重新赋值
- ✅ 可以调用不依赖具体值的成员（比如 `empty()` 是否合法取决于类型，`clear()` 安全）
- ❌ 不要读取它的值，也不要把「它一定是空」当成契约

标准库容器和 `std::string` 在实践中通常被搬空，但这是实现行为，不是语言保证：

```cpp
std::string a = "abcdefghijklmnopqrstuvwxyz";  // 超过 SSO 阈值（通常 15）
std::string b = std::move(a);
// a 现在通常是空串，但标准只保证「有效」
a = "reuse";      // 合法，重新赋值
```

**短字符串要特别注意**：如果字符串长度在 SSO（小字符串优化）范围内，数据存在对象内部的缓冲区里，移动时需要真的拷贝这些字节（因为是就地存储），源对象往往被置空但不是「指针搬走」。

## 返回局部变量：不要写 std::move

这是最经典的反模式：

```cpp
std::vector<int> make() {
    std::vector<int> v = {1, 2, 3};
    return std::move(v);   // ❌ 反而更慢
}
```

原因：编译器对「返回局部变量」有 **NRVO**（具名返回值优化），直接把 `v` 构造在调用方的位置上，零拷贝零移动。而一旦你写成 `std::move(v)`，返回值变成一个右值表达式，**NRVO 不再适用**，退化成一次移动构造。

正确写法就是 `return v;`。C++17 起，返回纯右值（prvalue）时的拷贝消除是**强制的**，连移动都不会发生：

```cpp
std::vector<int> make() {
    return {1, 2, 3};   // C++17 保证不产生临时对象
}
```

想验证可以在编译时加 `-fno-elide-constructors` 关掉优化，对比调用了几次构造/析构。

## std::forward 和 std::move 的区别

`std::move` 无条件转成右值；`std::forward` **按原样转发**（左值还是左值，右值还是右值）。模板转发必须用后者：

```cpp
template <typename T>
void wrapper(T&& arg) {          // 万能引用，不是右值引用
    inner(std::forward<T>(arg)); // ✅ 保持值类别
    // inner(std::move(arg));    // ❌ 即使传进来的是左值也会被当右值搬走
}
```

判断标准：

- **确定要把资源交出去** → `std::move`
- **只是把参数原样传给下一层** → `std::forward`

在模板里对万能引用用 `std::move`，会把调用方的左值悄悄搬空，这类 bug 极难排查——调用方传了个变量进来，函数返回后变量空了。

## 移动构造要标 noexcept

标准库容器在扩容时有一个选择：元素能否安全移动。判断依据是 `std::is_nothrow_move_constructible`：

```cpp
struct Widget {
    Widget(Widget&&) noexcept;              // ✅ vector 扩容时用移动
    Widget(const Widget&);                  // 拷贝构造
};
```

如果移动构造没有 `noexcept`，`vector` 在扩容时**会退化成拷贝**——因为移动过程中抛异常会导致容器处于不一致状态，标准库不敢用。这是「我明明写了移动构造，性能却没提升」的常见原因。

`std::string`、`std::vector` 这些标准类型的移动构造都是 `noexcept` 的，所以放进容器没问题；自己写的类型要记得标。

## 小结

| 写法 | 结果 |
| --- | --- |
| `std::move(x)` | 只是转成右值引用，不移动任何东西 |
| `T y = std::move(x)` | 调用移动构造，`x` 进入 valid-but-unspecified |
| `return std::move(local)` | ❌ 阻止 NRVO，反而多一次移动 |
| `return local;` | ✅ NRVO；C++17 对纯右值强制消除 |
| 模板转发用 `std::move` | ❌ 会搬空调用方的左值，应该用 `std::forward` |
| 移动构造不标 `noexcept` | ❌ 容器扩容时退化为拷贝 |

一句话记：**`std::move` 是「我允许你搬」，不是「我搬了」**；而要不要搬、怎么搬，取决于被调用的那个移动构造函数。
