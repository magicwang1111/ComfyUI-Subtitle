# ComfyUI-Subtitle

当前版本已改为：
- **输入视频上传到腾讯云 COS**
- **腾讯云 MPS 使用 COS 对象作为输入**
- **智能字幕与字幕压制输出写回腾讯云 COS**
- **最终字幕文件和压制后视频下载回本地输出目录**

> 当前采用快速验证方案：代码已移除阿里云 OSS 依赖，统一走腾讯云 COS + MPS。默认地域为 `ap-guangzhou`，默认 Bucket 为 `goumee-1444407842`。

## 配置方式

在插件根目录创建 `config.local.json`。

> `config.local.json` 仅用于本地凭证与环境配置，**不要提交到仓库**。

### 必填腾讯云配置
- `tencent_secret_id`
- `tencent_secret_key`
- `tencent_region`
- `tencent_mps_host`
- `tencent_mps_version`
- `tencent_request_timeout`
- `tencent_poll_interval`
- `tencent_max_wait_seconds`
- `tencent_subtitle_definition`
- `tencent_transcode_definition`

### COS 存储配置
- `tencent_cos_bucket`
- `tencent_cos_input_prefix`
- `tencent_cos_output_prefix`
- `tencent_cos_burn_output_prefix`

## 当前提供的前端节点

### 1. `Tencent Subtitle Burn`
一个主节点，内部自动完成：
- 本地视频输入（支持直接上传/选择 `video`，也支持 `file_path`）
- 上传视频到腾讯云 COS
- 调用腾讯云 MPS 发起智能字幕任务（COS 输入）
- 等待字幕任务完成
- 下载生成的字幕文件
- 生成本地字幕文件（`vtt / srt / ass`）
- 生成用于压制的 ASS 字幕并上传到腾讯云 COS
- 调用腾讯云 MPS 发起字幕压制任务
- 等待压制任务完成
- 下载压制后视频到本地

#### 输入
- `video`
- `file_path`
- `subtitle_format`
- `subtitle_position`
- `font_name`
- `font_size`
- `font_color`
- `font_alpha`
- `background_alpha`
- `accurate_mode`
- `need_wordlist`
- `adapt_words`
- `target_language`
- `subtitle_language_mode`
- `auto_wrap`
- `max_chars_per_line`
- `filename_prefix`
- `subtitle_definition_id`
- `transcode_definition_id`

#### 输出
- `video_file_path`
- `subtitle_file_path`
- `video_url`
- `subtitle_url`
- `status`
- `raw_json`

### 2. `Tencent Preview Video`
用于本地视频预览。

#### 输入
- `file_path`
- `video_url`（可选）
- `filename_prefix`
- `save_output`

#### 输出
- `file_path`

## 推荐工作流

```text
Tencent Subtitle Burn
  -> Tencent Preview Video
```

## example

- `examples/01_subtitle_burn_preview.json`

仓库中仅保留与当前节点实现匹配的示例工作流。

导入后：
1. 先配置好 `config.local.json`
2. 在主节点中填写本地 `file_path`，或者直接上传/选择 `video`
3. 设置字幕格式、位置、字体等样式
4. 运行后输出本地字幕文件和本地压制视频
5. 把 `video_file_path` 接到 `Tencent Preview Video`

## 当前实现说明

### 已经实现
- 腾讯云 COS 输入上传
- 主节点支持 `video` + `file_path`
- 腾讯云 MPS 以 COS 对象读取输入视频
- 字幕结果与压制结果输出到腾讯云 COS
- 预览节点保留音频并输出本地可预览视频

### 快速验证方案说明
- 当前版本优先验证纯腾讯云链路是否能顺利跑通
- 默认使用：
  - `tencent_region = ap-guangzhou`
  - `tencent_cos_bucket = goumee-1444407842`
- 若 COS 对象访问策略限制较严，可能需要你在腾讯云侧放宽测试对象的读取策略，确保 MPS 可读取上传后的字幕对象

所以这版代码当前目标是：先验证 COS 上传 + MPS 字幕生成 + MPS 字幕压制主链路是否跑通，再根据实际返回决定是否补充更严格的私有桶签名方案。
