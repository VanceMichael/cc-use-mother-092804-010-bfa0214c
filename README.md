# 非遗皮影剧场内容与互动资源排程

编排潮俗皮影演出、AI 动画、互动工作坊与非遗授权资源。

## 参与方与事实

主要参与方包括活动运营人员、皮影传承人、讲解与志愿者、参与游客、场地和物资管理员。领域资料记录以下已经确认的事实：

- 深圳海滨文化艺术区打造月下皮影剧场
- 活动包含主题展陈、皮影展演和互动工作坊
- 项目使用人工智能动画讲述中秋故事并推动非遗传承

## 业务约束

- 内容授权
- 演出版本
- 互动容量
- 教具库存
- 历史记录保留

`contracts/context.schema.json` 描述资料结构，`fixtures/context.json` 提供不含真实身份信息的示例，`src/shadow_play_context.py` 负责读取和校验这些资料。

## 活动后台领域模型

在领域资料之上，`src/shadow_play/` 实现了无外部依赖的纯 Python 领域层：

| 模块 | 职责 |
| --- | --- |
| `content.py` | 演绎内容版本（草稿/已确认/撤回）、AI 动画来源与适用范围标注、传承人授权申请/授予/拒绝/过期/撤销及历史 |
| `resources.py` | 教具套单位级台账（半开时间窗防双重分配、损坏退出与补位）、场地冲突检测、人员资质/语言校验与排班冲突 |
| `sessions.py` | 场次状态机、发布门（一次性返回全部待补事项）、预约、**幂等签到**、迟到、拆组、临时加场、损坏上报、取消与完成历史快照 |
| `views.py` | 负责人场次视图：内容依据、资源水位、责任人/排班、待补授权、发布障碍 |

典型流程（完整示例见 `tests/helpers.py`）：

```python
from datetime import date, datetime
from src.shadow_play import (
    ContentRegistry, ResourceRegistry, ProgramSystem,
    ContentKind, AiProvenance, RightHolder,
    KitType, KitUnit, Venue, Person, PersonRole, AssignmentFunction,
    SessionType, TimeWindow,
)

content, resources = ContentRegistry(), ResourceRegistry()
# 注册已确认剧本 + 传承人授权
script = content.submit_version("MULAN-01", ContentKind.SCRIPT, "木兰从军", now)
script.confirm(now)
grant = content.request_authorization(
    "MULAN-01:1", RightHolder("RH-01", "示例传承人"),
    ["performance", "workshop"], date(2026, 12, 31), now,
)
grant.grant(now)
# AI 动画必须带来源与适用范围
provenance = AiProvenance(
    model="示例动画模型", prompt_ref="PR-1", source_works=("MULAN-01",),
    scope_session_types=("performance",),
    valid_from=date(2026, 9, 1), valid_until=date(2026, 11, 30),
)
# …注册场地、教具套、人员；schedule_session 排期、use_content 绑内容、
# require_kit 定教具需求、assign_staff 排班；publish 过发布门后接受预约
system = ProgramSystem(content, resources)
```

关键规则速查见 [`docs/domain-rules.md`](docs/domain-rules.md)。

## 开发命令

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

编译检查：

```bash
python3 -m compileall -q src
```

两条命令只读取仓库内文件，不需要连接外部业务系统。
