# ComfyUI-Subtitle

当前版本已改为：
- **优先使用阿里云 OSS 作为输入视频与字幕文件上传链路**
- **腾讯云 MPS 通过 URL 读取输入**
- **但智能字幕生成和字幕压制的任务输出，当前实现仍需要腾讯云 COS bucket**

> 原因：你提供的腾讯云媒体处理文档明确支持 `URL` 输入，也明确支持通过 `StdExtInfo` 将部分转码输出写到第三方对象存储（含 OSS）。
> 但在智能字幕 `SmartSubtitlesTask` 和字幕压制 `ProcessMedia + SubtitleTemplate` 这两条主链路上，文档示例仍然都是 `OutputStorage -> COS`。因此当前实现已改成 **OSS 输入优先**，但 **MPS 输出仍保留 COS 依赖**。

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

### OSS 输入配置（当前主链路）
- `area`
- `oss_endpoint`
- `oss_access_key_id`
- `oss_access_key_secret`
- `oss_bucket`
- `oss_prefix`
- `oss_signed_url_expires`

### COS 输出配置（当前仍需要）
- `tencent_cos_bucket`
- `tencent_cos_output_prefix`
- `tencent_cos_burn_output_prefix`

## 当前提供的前端节点

### 1. `Tencent Subtitle Burn`
一个主节点，内部自动完成：
- 本地视频输入（支持直接上传/选择 `video`，也支持 `file_path`）
- 上传视频到 OSS
- 生成 OSS 签名 URL
- 调用腾讯云 MPS 发起智能字幕任务（URL 输入）
- 等待字幕任务完成
- 下载生成的字幕文件
- 生成本地字幕文件（`vtt / srt / ass`）
- 生成用于压制的 ASS 字幕并上传到 OSS
- 调用腾讯云 MPS 发起字幕压制任务（URL 输入）
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
- OSS 输入上传
- 主节点支持 `video` + `file_path`
- MPS 以 URL 方式读取输入视频和字幕文件
- 预览节点保留音频并输出本地可预览视频

### 当前实验模式说明
- 现在这版已经**硬试接入全 OSS 输出模式**
- 当未配置 `tencent_cos_bucket` 时，MPS 请求会尝试：
  - 不传 `OutputStorage`
  - 改为通过 `StdExtInfo` 注入 OSS 输出信息
- 这属于**实验模式**，因为你提供的文档明确写到第三方对象存储（含 OSS）可通过 `StdExtInfo` 配置，但没有直接给出 `SmartSubtitlesTask` 和字幕压制主链路的完整 OSS 输出示例

### 已确认的文档依据
- `URL` 输入：有明确示例
- `StdExtInfo + storage_type = oss`：有明确示例
- `SmartSubtitlesTask` / `SubtitleTemplate` 输出直接写 OSS：**没有直接示例**

所以这版代码已经按你的要求继续硬试，但是否真正跑通，要以真实接口返回为准。
