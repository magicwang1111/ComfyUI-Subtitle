# ComfyUI-Subtitle

当前版本已改为：
- **输入视频上传到阿里云 OSS**
- **腾讯云 MPS 使用 OSS 签名 URL 作为输入**
- **智能字幕与字幕压制均由腾讯云 MPS 完成**
- **压制后的成片写回腾讯云 COS，再下载到 ComfyUI 输出目录**

> 当前采用 OSS 输入 + 腾讯云 COS 输出的混合链路。默认腾讯云地域为 `ap-guangzhou`，默认 COS Bucket 为 `goumee-1444407842`。

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
- `tencent_cos_signed_url_expires`

### OSS 输入配置
- `area`
- `oss_endpoint`
- `oss_access_key_id`
- `oss_access_key_secret`
- `oss_bucket`
- `oss_prefix`：输入视频目录，默认 `GouMee-subtitle/input`
- `oss_signed_url_expires`

## 当前提供的前端节点

### 1. `Tencent Subtitle Burn`
一个主节点，内部自动完成：
- 本地视频输入（支持直接上传/选择 `video`，也支持 `file_path`）
- 上传视频到阿里云 OSS
- 调用腾讯云 MPS 发起智能字幕任务（URL 输入）
- 等待字幕任务完成
- 下载生成的字幕文件
- 生成本地字幕文件（`vtt / srt / ass`）
- 在本地生成用于压制的 ASS 字幕
- 把 ASS 上传到腾讯云 COS，并调用腾讯云 MPS 云端压制
- 下载 MPS 成片到本地输出目录，并返回 COS 签名 `video_url`

#### 输入
- `local_video`
- `video_file`（可选）
- `video_url`（可选）
- `vhs_video_info`（可选）
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
- 阿里云 OSS 输入上传
- 主节点支持 `video` + `file_path`
- 腾讯云 MPS 以 OSS 签名 URL 读取输入视频
- 智能字幕结果输出到腾讯云 COS
- 压制用 ASS 上传到腾讯云 COS
- 腾讯云 MPS 云端完成字幕压制，成片输出到 COS
- 成片下载到本地，并通过 COS `video_url` 交给下游预览节点
- 预览节点保留音频并输出本地可预览视频

### 存储链路说明
- 输入视频上传到 OSS，并通过限时签名 URL 交给 MPS
- 智能字幕文件通过 `OutputStorage` 写入腾讯云 COS
- 压制用 ASS 上传到 COS，并通过限时签名 URL 交给 MPS
- 压制成片由 MPS 写入 COS，再下载到 ComfyUI 本地输出目录
- `oss_signed_url_expires` 必须覆盖任务排队与处理时间，默认值为 86400 秒
