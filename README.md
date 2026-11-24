# 技术量词限定与工装规格签发

业务资料使用虚构编号和合成叙述。文本保持来件拼写和措辞。各表是独立的来源记录。
TSV 文件采用 UTF-8，首行为列名。行首 id 是该表的稳定记录号，空格和标点属于原文，空单元格表示来件未填。
日期时间采用所在地本地时间。资料内注明为编号列表的字段用竖线分隔。

## 资料字段

- `materials/specs.tsv`。lang 是正文语言，amends 表示补充函所指来文号，issued 是出具日。role 记录材料身份。
- `materials/gauges.tsv`。nominal 和 reading 与 uncertainty 共用 unit。uncertainty 是记录给出的对称半区间，空值表示没有提供。stage 为计量单来源写法。
- `materials/authorizations.tsv`。use 为实际投用的原始记录状态。serial 对应制造编号，station 是具体申请工位。
- `materials/measure_terms.tsv`。value 是原有计量术语登记。source 是登记术语适用的文书号，极限以文书原句为准。
- `materials/gauge_sources.tsv`。gauge 为计量记录编号，datum 和 fixture 是原表声明的基准与装夹号。

## 运行资料检查

使用 CPython 3.11.2 和标准库。资料检查仅读取输入。

```sh
python3 pack_check.py
python3 -m unittest -v
```

## 工装规格核对与签发判定（fixture_review）

`pack_check.py` 只校验物理格式；`fixture_review/` 在其之上做业务判定，同样只读。

```sh
python3 -m fixture_review.review_cli              # 全部工装的工位判定
python3 -m fixture_review.review_cli --serial V2  # 按交付工装查询
python3 -m fixture_review.review_cli --json       # 机器可读输出
```

判定口径（与业务约定一致）：

- **依据身份**：只有签署原规与有效补充函生成判据；供应商译稿、测点说明在确认前
  仅为沟通线索，不参与放行。原文、译稿、补函各自保留段落与来文 id。
- **结构化要素**：数值单位、上下限开闭（“严格小于”保留为开口）、相对公称值的偏差、
  测量面与测量时点分别落字段。单位换算只用于数值比较，不消解原文要求的对象或条件。
- **译差定位**：只把会改变测量判读的差异（对象合计化、时点变化、开闭变化、数值变化）
  列为实质译差并给出双语出处；单纯措辞差异不进入供方解释队列。
- **区间三态**：整个不确定区间满足要求才自动认可；整段超出则明确拒绝；
  跨边界、缺决定性测点或无不确定度且读数恰在开口边界时要求复测，并写明应补条件。
- **测点映射**：供方线索默认“未确认”，质量工程师可确认测点与适用修订但不替代测量；
  复校驳回优先且不可被重复来文翻案。一张报告内其他可确认测点不受牵连，
  依赖被驳回映射的合并报告拆回复测。
- **双测单合用**：同一工装两边分测时，计量基准与最近一次装夹标识都相同才能合用，
  否则合并报告拆回复测。
- **装机例外**：补函的装机后测法只对实际已装机夹具生效，且须同时核对装机事实与
  是否明确更换测点；未装机夹具继续走装机前原限。
- **签发口径**：按具体工装、具体工位发放。主、备工位各自判断，备工位通过不放行主线。
  更严修订到达时，未投入签发须重审；已按旧版投用的显示“待工程处理”并附历史依据；
  同一工装在其他工位仍按自身条件办理。

### 模块划分

- `fixture_review/spec_parser.py`：文书身份识别与签署文书的句式解析，未识别句式进告警
- `fixture_review/translation.py`：译稿声明解析与双语实质译差定位
- `fixture_review/mapping.py`：测点映射登记、工程师确认与复校驳回生命周期
- `fixture_review/measurements.py`：单位换算、不确定区间三态判定、测单合用条件
- `fixture_review/engine.py`：按（工装、工位）汇总证据、适用修订选择与签发标记
- `fixture_review/review_cli.py`：接收组查询入口（文本/JSON）
