> [English](rtpc-math.md) | **简体中文**

# RTPC 数学与输出语义

本文档精确定义导出数字的含义，以便对照 `examples/` 中的 CSV 手工核验。

## 记号

| 符号 | 含义 |
|---|---|
| `R` | RPM |
| `NativeRPM` | 给定循环的标称 RPM 标签 |
| `MeasuredPitch` | 该循环求解出的声学音高，单位为音分（相对锚点） |
| `ModelPitch(R)` | 拟合出的主映射在 `R` 处的取值，单位为音分 |
| `AssetOffset` | 在 Wwise 中施加到某个特定循环的整数音分偏移 |

## 1. 两种输出，以及为什么两者都需要

一个资源在任意 RPM 下的最终音高，是逐资源常数与逐资源曲线之和：

```text
FinalPitch(R) = AssetOffset + RTPCPitch(R)
```

- **`AssetOffset`** 是单个整数音分数。它修正的是该循环自身音高与主映射在该循环原生 RPM 处的*恒定*不一致。
- **`RTPCPitch(R)`** 是让循环随 RPM 一起移动的曲线。它是手工编写的 Smart Pitch Curve 的替代品。

这样拆分修正意味着曲线**可以跨资源复用**：每个循环都能共享同*一类*曲线，只有常数不同。

## 2. 计算资源偏移

```text
RawAssetOffset = ModelPitch(NativeRPM) − MeasuredPitch
AssetOffset    = round_to_integer(RawAssetOffset)     # Wwise integer cents
```

这里的取整是真正的量化：Wwise 的 Asset Offset 是整数。**未取整**的值在导出中保留为 `RawAssetOffsetCents`，因此残差是可见的，而不是被藏在取整里。这一列就是诊断依据——原始偏移为 0.13 音分的资源对得很准；12.4 音分的则不是。

## 3. 计算 RTPC 曲线

```text
RTPCPitch(R) = ModelPitch(R) − ModelPitch(NativeRPM)
```

由此直接得到两条性质，两者都很重要：

1. **曲线在该资源自身原生 RPM 处恰好为 0。** 由构造可知 `RTPCPitch(NativeRPM) = 0`，这就是逐资源图表在该资源自身 RPM 处穿过零点的原因。
2. **映射是可加的。** 由于曲线是相对其自身原生 RPM 表达的，它与 `AssetOffset` 组合时不需要第二个修正项。

正音分升高音高，负音分降低音高。

## 4. 主映射

```text
ModelPitch(R) = a · log(R + K) + b + residual(R)
```

- `log(R + K)` —— 物理基线。`K` 的存在使该表达式在 RPM 接近零时仍然有定义且条件良好。
- `residual(R)` —— 单调的非参数修正项，使模型既能跟随真实引擎的非理想行为，又保持严格非递减。
- 用 **PCHIP** 求值，选择它是因为它保单调；穿过同样这些点的三次样条可能过冲并引入局部下降，从而产生一条在某个 RPM 处反向的音高曲线。
- 外推被**禁用**：在测量到的 RPM 范围之外，模型不做猜测。

## 5. 音分与 Wwise 限制

不可用范围的上限是 **±2400 音分**（`wwise_pitch_limit_cents`）。超出该范围的结果会在输出中被标记；它们**绝不会被静默钳制**。一个被静默钳制的值会看起来像一次成功的分析，却掩盖了测量或输入本身有错这一事实。

## 6. CSV 里有什么

`examples/example_asset_offsets.csv`：

| 列 | 含义 |
|---|---|
| `Asset` | 循环文件名 |
| `NativeRPM` | 标称 RPM 标签 |
| `MeasuredPitch` | 求解出的声学音高（音分，已锚定） |
| `ModelPitch` | 主映射在 `NativeRPM` 处的值 |
| `RawAssetOffsetCents` | 未取整的 `ModelPitch − MeasuredPitch`（诊断用） |
| `AssetOffsetCents` | 要在 Wwise 中施加的整数 |
| `Confidence` | 0–1 的测量置信度 |
| `Status` | `OK`，或诸如 `WARNING:LOW_CONFIDENCE`、`...|OUTLIER` 的诊断 |

`examples/example_rtpc_control_points.csv`：

| 列 | 含义 |
|---|---|
| `Asset` | 循环文件名 |
| `NativeRPM` | 标称 RPM 标签（该资源曲线的零点） |
| `rtpc_RPM` | RTPC x 值：RPM 轴 |
| `RTPCPitchCents` | 该 RPM 处的 RTPC y 值 |
| `AssetOffsetCents` | 该资源的常数偏移 |
| `FinalPitchCents` | `AssetOffset + RTPCPitch` —— 最终的音高 |
| `Status` | 该点的状态 |

## 7. 这些数字不意味着什么

- **它们是相对的，不是绝对的。** 整个系统锚定在一个资源上，因此「音高」指的是「相对锚点的音高」。设定载具的绝对音高仍然是艺术决策。
- **它们假设 RPM 标签大致正确。** 两阶段搜索能容忍约 ±1200 音分蕴含音程的标签误差，但一个标错得很厉害的文件会让它失效。
- **它们假设循环是稳态的。** 扫频音高违背了「一个循环只有一个音高」这一前提。