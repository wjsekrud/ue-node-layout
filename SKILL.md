---
name: ue-node-layout
description: Auto-arranges Unreal Engine node graphs (Blueprint Event/Function/Macro graphs, Material graphs, other EdGraph-based graphs) so they are readable — exec flow left→right, branches on separate rows, data nodes tucked left of their consumers, event chains stacked and optionally wrapped in comment boxes. Use whenever nodes were created or edited through a UE MCP server (they usually end up in one long line), when planning node positions BEFORE creating nodes via MCP, or when the user asks to tidy/organize/arrange/정리/정렬 블루프린트·머티리얼 노드, "노드가 일렬로 늘어서 있다", "가독성 있게 배치". Also works with no MCP at all via copied node text (Ctrl+C clipboard / T3D).
---

# UE 노드 그래프 자동 정리

노드의 **위치만** 바꾼다. 노드 추가·삭제, 연결 변경, 핀 기본값 수정은 절대 하지 않는다 (코멘트 박스 추가는 예외이며 선택 사항).

레이아웃 계산은 전부 `scripts/ue_layout.py`가 한다. 좌표를 손으로 계산하지 말 것 — 겹침 없는 결과가 보장되지 않는다.

- 스크립트 경로: 이 SKILL.md 옆의 `scripts/ue_layout.py` (보통 `~/.claude/skills/ue-node-layout/scripts/ue_layout.py`)
- 실행: Windows는 `py`, 그 외는 `python3` (Windows의 `python`은 스토어 스텁일 수 있음)
- 외부 패키지 필요 없음 (표준 라이브러리만)

## 만들어지는 배치

- 실행(exec) 흐름은 왼쪽 → 오른쪽 한 줄.
- 첫 번째 exec 출력은 같은 줄로 이어지고, 나머지(Else, Completed, Then 1, …)는 겹치지 않게 아래 줄로 내려간다.
- 순수(pure)/데이터 노드는 그 값을 쓰는 노드의 **왼쪽 아래**에 핀 순서대로 트리 형태로 놓인다 (exec 와이어를 가리지 않음).
- 이벤트(루트)별 체인은 위아래로 쌓인다. `--comments`를 주면 체인마다 코멘트 박스가 생긴다.
- exec 핀이 없는 그래프(머티리얼 등)는 최종 출력 노드에서 시작해 오른쪽 → 왼쪽 트리로 배치된다.
- 16px 그리드에 스냅되고, 원래 그래프의 좌상단 위치는 유지된다 (`--origin X Y`로 바꿀 수 있음).

## 1단계: 어떤 경로로 할지 정하기

현재 세션에 있는 UE MCP 도구를 확인한다 (도구 이름에 blueprint / node / graph / material / pin / python 같은 단어가 들어간 것). 필요하면 ToolSearch로 찾는다. 그다음 아래에서 가능한 가장 위쪽 경로를 고른다.

| 경로 | 조건 | 쓰는 때 |
|---|---|---|
| **A. MCP로 읽고 옮기기** | 노드 목록(id, 핀, 연결)을 읽는 도구 **와** 노드 위치를 바꾸는 도구(move/set position/set property, 또는 에디터 Python 실행)가 있음 | 이미 있는 그래프 정리 |
| **B. 미리 배치 계획** | 노드 생성 도구가 위치 인자(node_position, x/y 등)를 받음 | **Claude가 지금 MCP로 노드를 새로 만드는 중**일 때 (항상 이 방법을 우선) |
| **C. 클립보드(T3D)** | 위 도구가 없거나, 실패하거나, 사용자가 직접 만든 그래프 | MCP 없이도 항상 가능 |

## 경로 A: MCP로 읽고 → 계산하고 → 옮기기

1. MCP로 대상 그래프의 노드를 전부 읽는다. 노드마다 필요한 것: 고유 id, 표시 이름(title), 현재 x/y, 핀(이름, 방향, exec 여부), 연결.
2. 아래 형식으로 JSON을 만들어 스크래치패드(임시 폴더)에 저장한다.
3. `py scripts/ue_layout.py json graph.json -o layout.json --svg preview.svg` 를 실행한다.
4. `layout.json`의 `positions`대로 노드마다 MCP 위치 변경 도구를 호출한다. 일괄 변경 도구가 있으면 그것을 쓴다.
5. `--comments`를 썼고 MCP에 코멘트 생성 도구가 있으면 `comments`의 박스(x, y, w, h, title)대로 만든다. 없으면 생략한다.
6. 다시 읽어서(또는 스크린샷 도구가 있으면 찍어서) 확인하고, 블루프린트는 컴파일/저장이 필요하면 해 둔다.

MCP가 위치 변경 도구 없이 **에디터 Python 실행**만 제공하면, 먼저 작은 노드 하나로 위치 변경이 실제 반영되는지 시험한다 (`EdGraphNode`의 `NodePosX/NodePosY`는 Python에 노출되지 않는 엔진 버전이 많다). 안 되면 바로 경로 C로 넘어간다.

### JSON 입력 형식

```json
{
  "nodes": [
    {"id": "N1", "title": "Event BeginPlay", "x": 0, "y": 0,
     "pins": [{"name": "then", "dir": "out", "kind": "exec"}]},
    {"id": "N2", "title": "Print String", "x": 300, "y": 0,
     "pins": [{"name": "execute", "dir": "in", "kind": "exec"},
              {"name": "then", "dir": "out", "kind": "exec"},
              {"name": "In String", "dir": "in", "kind": "data"}]}
  ],
  "edges": [
    {"from": "N1", "fromPin": "then", "to": "N2", "toPin": "execute", "kind": "exec"}
  ]
}
```

- 연결은 `edges`로 주거나, 핀 안에 `"links": [{"node": "N1", "pin": "then"}]`로 줘도 된다 (둘 다 줘도 중복 처리됨).
- `pins`는 생략할 수 있다. 생략하면 edges에 나온 순서대로 핀이 만들어진다. 단 **핀 순서가 데이터 노드의 세로 순서를 정하므로** 알 수 있으면 실제 핀 순서대로 넣는다.
- exec 판정: MCP가 핀 타입을 주면 `exec` 카테고리인 핀이 exec다. 타입 정보가 없으면 이름으로 추정한다 (execute, then, else, exec, Completed, Loop Body, then_0…, True/False(Branch), A/B(Sequence 출력)…). exec 핀이 하나라도 있는 노드는 실행 노드, 없는 노드는 순수 노드로 처리된다.
- 노드 크기(`w`, `h`)를 MCP가 알려주면 넣는다. 없으면 제목·핀 이름 길이로 넉넉하게 추정한다.
- 기존 코멘트 노드는 `"comment": true`로 표시하면 배치에서 빠진다.

## 경로 B: 노드를 만들기 전에 배치 계획

MCP로 그래프를 새로 만들 때는 노드를 만들고 나서 정리하지 말고, **만들기 전에 좌표를 정한다.**

1. 만들 노드와 연결을 위 JSON 형식으로 먼저 적는다 (x/y는 생략하거나 0).
2. `py scripts/ue_layout.py json plan.json -o layout.json --origin <X> <Y>` 로 계산한다. 기존 노드와 겹치지 않게 origin은 기존 그래프 아래쪽 빈 곳으로 잡는다 (예: 기존 노드 최대 y + 400).
3. 노드를 만들 때 계산된 좌표를 위치 인자로 넘긴다. 그다음 연결한다.
4. 만들고 나서 실제 노드가 계획과 다르면(MCP가 핀을 더 만들었거나 등) 경로 A로 한 번 더 정리한다.

## 경로 C: 클립보드(복사한 노드 텍스트)

UE 에디터에서 노드를 복사하면 클립보드에 텍스트(T3D)가 들어가고, 여기에는 위치(NodePosX/Y)와 연결(LinkedTo)이 전부 들어 있다. 스크립트가 이 텍스트의 좌표만 바꿔서 클립보드에 다시 넣는다.

사용자에게 다음 순서를 안내한다:

1. 정리할 그래프 탭에서 **Ctrl+A** (전체 선택) → **Ctrl+C** (복사). 일부만 정리하려면 그 노드들만 선택한다. 단 선택 밖의 노드와 이어진 연결은 붙여넣을 때 끊긴다.
2. (Claude 실행) `py scripts/ue_layout.py clip --comments`
   - 원래 클립보드 내용은 임시 폴더의 `ue_layout_clipboard_backup.txt`에 백업된다.
   - 사용자가 텍스트 파일을 줬다면: `py scripts/ue_layout.py t3d in.txt -o out.txt --comments`
3. 에디터로 돌아가서 (선택이 유지된 상태로) **Delete** → 그래프 빈 곳에 마우스를 두고 **Ctrl+V**.
4. 결과가 마음에 들지 않으면 **Ctrl+Z**를 두 번 누르면 원래대로 돌아간다.

알려 줄 주의점:
- **함수 그래프의 Entry/Result 노드**는 삭제·붙여넣기가 안 되므로 그 자리에 남고, 거기에 이어진 와이어(보통 1~2개)는 다시 연결해야 한다. 함수 그래프는 가능하면 경로 A를 쓴다.
- 머티리얼 그래프의 최종 출력 노드도 복사되지 않는다. 출력 노드를 빼고 붙여넣으면 출력으로 가는 연결이 끊기므로 머티리얼은 경로 A를 권장한다. 경로 C로 하려면 출력 노드와 연결된 노드는 다시 연결해야 한다고 미리 알린다.
- 붙여넣기를 하면 노드가 새로 생성되므로 노드 이름/GUID가 바뀐다. 동작은 같다.

## 옵션

| 옵션 | 의미 |
|---|---|
| `--comments` | 이벤트(루트) 체인마다 코멘트 박스 (클립보드/T3D 모드에서는 블루프린트 그래프에만 추가됨) |
| `--spacing 1.3` | 모든 간격을 1.3배로 (빽빽하다고 하면 올림) |
| `--origin X Y` | 결과 좌상단 위치 |
| `--svg file.svg` | 미리보기 SVG. 배치를 적용하기 전에 확인할 때 쓴다. Windows에서는 Edge 헤드리스로 PNG를 만들어 볼 수 있다: `msedge --headless=new --screenshot=out.png --window-size=1600,900 file:///.../file.svg` |

## 끝나고 보고할 것

- 몇 개 노드를 몇 개 체인으로 정리했는지 (스크립트가 `nodes=… chains=…`를 출력함)
- 경로 C였다면 사용자가 할 일(Delete → Ctrl+V)과, 다시 이어야 할 와이어가 있는지
- MCP가 일부 노드 이동에 실패했다면 그 노드 목록
