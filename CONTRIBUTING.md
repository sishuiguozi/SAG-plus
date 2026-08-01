# Contributing to SAG-plus

SAG-plus 是 [Zleap-AI/SAG](https://github.com/Zleap-AI/SAG) 的个人优化分支。
贡献应聚焦本地检索质量、入库可靠性、存储维护或桌面开发体验。

## 开发验证

在验证桌面改动时，只使用：

```bash
cd /e/SAG-plus/apps/desktop
npm run dev
```

提交前请保留现有测试与格式检查，并在改动检索、向量写入、分块或存储逻辑时补充
对应测试。不要提交 `.data`、数据库、上传文件、模型缓存或密钥。

上游归属、许可证和原始代码历史保持不变。
