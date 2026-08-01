# 原生支持源码/标记文件入库

## 背景

当前 `allowed_upload_exts` 白名单只含文档类格式,代码文件上传即被 `_check_extension` 拒绝;即便绕过白名单,解析层只把 `.txt/.text/.log` 当纯文本(`is_plain_text_path`),其余扩展名落给 MarkItDown--而 MarkItDown 不认识代码扩展名,会解析失败。

前端 `allowedExts` 来自后端 system endpoint(`settings.allowed_upload_exts`,见 [system.py:43](apps/api/sag_api/api/v1/system.py#L43)),无硬编码--改后端白名单即自动生效(文件选择器 `accept` + 客户端预校验都会跟上)。

## 将支持的完整扩展名清单(均为纯文本)

- **C / C++**:`.c` `.h` `.cpp` `.hpp` `.cc` `.cxx` `.hh` `.hxx`
- **C#**:`.cs`
- **Python**:`.py`
- **Java**:`.java`
- **Perl**:`.pl`
- **reStructuredText**:`.rst`
- **AFSIM**(实测 ASCII 纯文本):`.fxw` `.ag`

> `.fxw` / `.ag` 已在用户提供的 `afsim-2.9.0-win64` 目录中实测为 ASCII 纯文本(CRLF)。

## 改动

### 1. `apps/api/sag_api/core/config.py` - 上传白名单
在 `allowed_upload_exts`([config.py:69-85](apps/api/sag_api/core/config.py#L69-L85))集合中加入上述 16 个扩展名。

### 2. `apps/api/sag_api/parsing/text.py` - 纯文本后缀集
在 `_PLAIN_TEXT_SUFFIXES`([text.py:17-21](apps/api/sag_api/parsing/text.py#L17-L21))中加入同样 16 个扩展名。
- `is_plain_text_path` 对它们返回 True -> 解析走 `_convert_plain_text`(直读 + 编码识别 + 文本质量校验),而非 MarkItDown(见 [service.py:148-153](apps/api/sag_api/parsing/service.py#L148-L153))。
- `_TEXT_PREVIEW_SUFFIXES = _PLAIN_TEXT_SUFFIXES | {...}` 自动继承 -> `is_text_preview` 也返回 True,`/preview` 接口返回文本而非强制下载。

### 3. `apps/api/tests/test_document_parsing.py` - 新增测试
参考 `test_legacy_gb18030_text_is_normalized_without_markitdown`:写一个 `.cpp` 文件,monkeypatch `_markitdown_sync` 使其 raise(断言不被调用),验证 `prepare_document` 走纯文本路径并返回文件原文。

## 不改动
- **前端**:`allowedExts` 由后端驱动,自动生效;i18n 文案「支持 Markdown / 文本 / PDF 等」用「等」字已涵盖,无需改。
- **解析调用链**:`prepare_document` 是唯一入口,`jobs/tasks.py` 等调用方无需改动。

## 安全性 / 边界
- **大小写**:`_check_extension` 与 `is_plain_text_path` 均 `.lower()`,大写 `.FXW`/`.JAVA` 等也可匹配(实测 AFSIM 存在大写 `.FXW`)。
- **二进制安全网**:若有人上传同名扩展名的二进制文件,`_assert_text_quality` 检测 NUL 字节/过多控制字符 -> `TextDecodingError` -> `ValidationError`,不会污染知识库。
- **编码**:解码器支持 UTF-8 / GBK / GB18030 / Big5 / Shift-JIS;源码文件多见 UTF-8/ASCII,无障碍。

## 验证
- `pytest apps/api/tests/test_document_parsing.py`
- 手动:上传一个 `.py` / `.cpp` / `.fxw` 文件,确认入库成功、解析为 Markdown、预览可读。
