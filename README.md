# nodaroo-dashboard

跑在 Nodaroo 免费机上的个人聚合面板：天气、RSS 订阅、快捷入口、Simular 盒子状态，单文件纯标准库。

## 部署

1. 把 `app.py` 放到 Nodaroo 的 `/home/container/`；
2. 面板环境变量加 `AUTO_UPDATE=1`（配合 git 仓库自动更新）；
3. 点 Restart，浏览器访问分配的端口即可。

## 个人配置

在盒子目录建 `config.json`（参考 `config.example.json`，不进仓库）：

```json
{
  "city": "深圳", "lat": 22.5431, "lon": 114.0579,
  "rss_feeds": [["名称", "https://..."]],
  "quick_links": [["名称", "https://..."]],
  "refresh_interval": 600
}
```

Simular 盒子状态（可选）：面板环境变量加 `SIMULAR_KEY`。
