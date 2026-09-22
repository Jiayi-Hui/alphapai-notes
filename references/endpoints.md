# Verified AlphaPai web endpoints

Everything below was observed against the live site by intercepting the
application's own requests. No request was forged and no header was read.
Paths are relative to `https://alphapai-web.rabyte.cn/external/alpha/api/`.

All envelopes look like `{code, message, data}`; the web app signals success
with `code: 200000`.

## Routes

| Section | Path | Proven by |
| --- | --- | --- |
| 转记 | `/reading/paipai/index?menu=convert` | `mix/record/convert/task/history/list` |
| 发现 | `/reading/home/my-focus` | `reading/information/flow/...` |
| 个股 | `/reading/home/stock` | `information/flow/stock/follow` |
| 研报 | `/reading/home/report` | `reading/report/list/v2` |
| 板块 | `/reading/home/segment` | `reading/sector/...` |

Paths that look plausible but 404: `/reading/discover/index`,
`/reading/discover/hotDiscussion`, `/reading/stock/index`. This is why routes
are rediscovered rather than hard-coded.

## 转记

`POST mix/record/convert/task/history/list` - paged list.

```
data: { pageNum, pageSize, totalPageNum, totalSize, data: [ row ] }
```

Row fields that matter:

| Field | Meaning |
| --- | --- |
| `id` | opaque record id, **re-encrypted every session** - never persist it |
| `title`, `uploadFileName` | display title |
| `createTime`, `durationDetail` | creation time, duration in seconds |
| `status` | 1 = 已转记 (done) |
| `example` | AlphaPai's seeded demo rows |
| `summaryDocxUrl` | object-store key for the AI 纪要 |
| `summaryRadioDocxUrl` | object-store key for the 逐字稿 |
| `originMediaUrl` | object-store key for the recording |
| `uploadFileUrl` | original upload, or an http link for 链接转记 |

`GET mix/record/convert/task/progressing/list` - in-flight tasks.

### Download

The `*Url` fields are **object-store keys, not links**:

```
alphapie_user_audio/AI_summary/2024/09/29/AI纪要_88215_....docx
```

Choosing a leaf in the row's `下载` submenu calls:

```
GET reading/recordconvert/download?id=<opaque>&source=ai&summaryType=correct
  -> {code: 200000, data: {type: "data", url: "<object-store key>"}}
```

The app then navigates to
`https://alphapai-storage.rabyte.cn/summary/<urlencoded key>`.

Three things were tested and are dead ends:

- `GET` that storage URL with the browser's own context: **401**.
- `fetch()` it from page context: blocked by **CORS**.
- Headless Edge: the navigation produces **no download event at all**.

So the artefact is obtained from the `download` event in a headed browser.

## 发现 boards

| Board | Endpoints |
| --- | --- |
| 机构热议 | `mix/hot/topic/current/batch/list`, `mix/hot/topic/stock/list`, `mix/hot/topic/report/latest/v2` |
| 推荐 | `reading/report/hot/recommend`, `reading/roadshow/summary/hot/recommend`, `reading/stock/hot/recommend` |
| 分析师 | `reading/information/flow/analyst/information/list` |
| 自选 | `reading/information/flow/stock/follow/query`, `.../stock/information/list` |
| 板块 | `reading/sector/prosperity/list`, `reading/sector/market/performance`, `reading/sector/chain/top/list` |

### Response shapes

Four envelopes occur, and the last one is common enough that ignoring it makes
whole boards look empty:

```
data: [ row ]                                  bare list
data: { data: [ row ], totalSize }             paged list
data: { list: [ row ] }                        simple list
data: { groupA: [ row ], groupB: [ row ] }     several named lists
```

Grouped examples:

- analyst -> `informationFlow`, `roadshowSummary`, `comment`, `report`
- 机构热议 rankings -> `publicList`, `privateList`, `insuranceList`
  (公募榜 / 私募榜 / 保险榜)

Rows extracted from a grouped envelope carry `_group`.

Note: `information/flow/stock/follow/query` returns only `{code, follow}` -
stock names are not in that payload.

## Observed empty-but-healthy case

For an account following no analysts, the analyst feed answers `200 / 200000`
with every group `null`. That is an empty feed, not a broken capture - which
is why a zero-row board reports whether its endpoint answered.

## DOM notes for the 转记 list

- Row action trigger: `.th-more__trigger`; menu items `.th-more__item`
  (`同步至PaiWork`, `下载 >`); submenu leaves `.th-more__subitem`
  (`全部`, `转记`, `AI纪要`, `录音文件`).
- **The DOM order of these elements does not follow the visible row order**,
  and every row keeps its (hidden) menu in the DOM. Selecting by index or by
  text across the document therefore operates on the wrong record - which
  produced notes whose body belonged to the neighbouring meeting. Rows are
  matched by the vertical position of their title, and the submenu leaf is
  resolved with `elementFromPoint` on the menu that was just opened.
- A synthetic pointer click never lands on a leaf: the submenu overlay
  intercepts pointer events. The click must be dispatched on the node.
