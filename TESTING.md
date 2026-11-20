# 测试记录

本次改动的实际验证结果。运行：

```bash
cd backend
python -m tests.run_all
```

各模块使用独立 SQLite 数据库与子进程，无需 MySQL/Redis/Docker。生产运行所需依赖见 `requirements.txt`；测试还需 `sqlalchemy`、`pydantic`、`fastapi`、`redis`、`PyJWT`、`httpx`（FastAPI TestClient）。

## 覆盖与结果（2026-11-20，Python 3.11，NumPy 2.4）

| 测试 | 内容 | 结果 |
|---|---|---|
| `test_segmentation.test_matches_oracle` | n=40/60/95/200 × 罚分 0/5/40/400，对比独立 O(n²) 动态规划：最优目标值与**全部**最优方案必切位置 | 通过 |
| `test_segmentation.test_constant_porosity_mean_model` | 孔隙率恒定段只拟合均值，RSS 等于围绕均值的平方和，斜率为空 | 通过 |
| `test_segmentation.test_sequence_sorted_and_validated` | 乱序生产序号排序；重复/缺失/非正/不足20件全部拒绝 | 通过 |
| `test_segmentation.test_resume_prefix_not_recomputed` | 分别在前向、后向、必切校验阶段中断再续算；续算跳过已确认前缀，目标值/分段/必切位置与一次性计算完全一致 | 通过 |
| `test_segmentation.test_checkpoint_invalidated_by_penalty` | 罚分改变后检查点不复用 | 通过 |
| `test_segmentation.test_100k_not_enumerated` | 100,000 件约 9 秒完成（含检查点写盘，文件约 5 MB），非枚举 | 通过 |
| `test_worker_resume` | 真实 worker 中断→重启→结果与全新计算逐行一致；`progress` 保留已确认进度 | 通过 |
| `test_services` | 冻结快照按序号排序；运行后改测值不改历史；不完整结果不能复核/导出；重复序号被数据库拒绝；缺序号阻止发起 | 通过 |
| `test_api` | 登录/角色；分页与参数校验；重复序号 409；缺序号 422；未知参数 422；观察员发起 403、导出未复核 403、复核前 409、复核后 200；二次复核 409；审计留痕 | 通过 |

## 关键回归
- 曾出现必切位置在续算后从 33 处变为 203 处：根因是检查点未持久化 DP 边界 `f[0]`/`g[n]`，续算载入为 `inf`。已修复并在 `open()` 每次恢复边界，`test_resume_prefix_not_recomputed` 覆盖三个阶段的续算一致性。
