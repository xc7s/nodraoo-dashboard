# nodaroo-dashboard

跑在 Nodaroo 免费机上的聚合站：打开即公众主页（深圳天气、工具集、新闻、注册用户公开内容），登录后可发布内容和管理账号。单文件纯标准库。

## 部署

1. 把 `app.py` 放到 Nodaroo 的 `/home/container/`；
2. 面板环境变量加 `AUTO_UPDATE=1`（配合 git 仓库自动更新）；
3. 点 Restart，浏览器访问分配的端口即可。

## 页面

- `/` 公众主页：天气（默认深圳）、工具集、新闻、注册用户公开内容
- `/login` `/register` 登录与注册
- `/panel` 我的面板（Simular 盒子状态，需登录）
- `/admin` 管理（仅管理员）：改邀请码、调人数上限、代建/移除账号

## 用户系统

- 邀请码注册，初始邀请码 `nodaroo`；注册上限 5 人
- 首位注册者自动成为管理员，可在 `/admin` 改邀请码和人数上限
- 首位用户可不填用户名（仅密码登录），此时注册关闭，此后账号只由管理员增删
- 登录后可在主页发布公开内容，可删除自己发的内容
- 账号数据存在盒子的 `users.json` / `posts.json`（不进仓库），密码只存加盐哈希

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
