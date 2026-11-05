from __future__ import annotations

import unittest

from dma.agent import (
    PendingFile,
    _parse_call_payload,
    extract_file_blocks,
    parse_tool_calls,
    strip_tool_blocks,
)


class AgentParseTests(unittest.TestCase):
    def test_parse_fence(self) -> None:
        text = """先读入口。

```tool
{"tool":"read_file","path":"src/main/java/A.java"}
```

```tool
[{"tool":"list_files","path":".","depth":2},{"tool":"search_text","query":"Registry"}]
```
"""
        calls = parse_tool_calls(text)
        self.assertEqual([item.name for item in calls], ["read_file", "list_files", "search_text"])
        self.assertEqual(calls[0].args["path"], "src/main/java/A.java")
        self.assertEqual(calls[2].args["query"], "Registry")

    def test_parse_tag_and_flat_args(self) -> None:
        text = '<tool>{"name":"remember","content":"用 Yarn 映射"}</tool>'
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "remember")
        self.assertEqual(calls[0].args["content"], "用 Yarn 映射")

    def test_parse_unclosed_tag_variants(self) -> None:
        variants = [
            '<tool {"tool":"read_file","path":"build.gradle"}',
            '<tool>{"tool":"read_file","path":"build.gradle"}',
            '<tool {"tool":"read_file","path":"build.gradle"}</tool>',
            '<tool>\n{"tool":"read_file","path":"build.gradle"}\n</tool>',
        ]
        for text in variants:
            calls = parse_tool_calls(text)
            self.assertEqual(len(calls), 1, text)
            self.assertEqual(calls[0].name, "read_file")
            self.assertEqual(calls[0].args["path"], "build.gradle")
            self.assertEqual(strip_tool_blocks(text), "", text)

    def test_invalid_json_ignored(self) -> None:
        self.assertEqual(parse_tool_calls("```tool\nnot json\n```"), [])
        self.assertEqual(_parse_call_payload("[]"), [])
        self.assertEqual(_parse_call_payload('{"args":{}}'), [])

    def test_parse_dsml_wrapped_tool_calls(self) -> None:
        variants = [
            # 实际截图里的写法：DSLM、标签里带空格、闭合标签是 </|DSML|...>
            '< | DSLM | calls > < | DSLM | invoke name="tool"> '
            '<tool>{"tool":"read_file","path":"src/main/java/A.java",'
            '"offset":42000,"max_chars":14000} </|DSML|parameter> '
            "</|DSML|invoke></|DSML|calls>",
            # 没有 <tool> 外壳，直接是 JSON；闭合标签写成 <|/DSML|...>
            '<|DSML|calls><|DSML|invoke name="tool">'
            '{"tool":"read_file","path":"src/main/java/A.java","offset":42000}'
            "<|/DSML|parameter><|/DSML|invoke><|/DSML|calls>",
        ]
        for text in variants:
            calls = parse_tool_calls(text)
            self.assertEqual(len(calls), 1, text)
            self.assertEqual(calls[0].name, "read_file")
            self.assertEqual(calls[0].args["path"], "src/main/java/A.java")
            self.assertEqual(strip_tool_blocks(text), "", text)

    def test_dsml_edit_file_with_unescaped_quotes(self) -> None:
        # 实际截图：old / new 里直接放 Java 代码，双引号没有转义。
        text = (
            "先加工具方法并改 snapshot。\n\n"
            '< | DSLM | calls > < | DSLM | invoke name="tool">\n'
            '{"tool":"edit_file","path":"src/main/java/com/darkitten/possession/mca/McaCompat.java",'
            '"old":"    CompoundTag snapshot = new CompoundTag();\\n'
            '    snapshot.putString("phs_name", resolveVillagerName(villager));",'
            '"new":"    CompoundTag snapshot = new CompoundTag();\\n'
            '    snapshot.putString("phs_name", resolveVillagerName(villager));\\n'
            '    snapshot.put("phs_relations", captureRelations(villager.getRelationships().getFamilyEntry()));"}'
            " </| DSML | parameter> </| DSML | invoke> </| DSML | calls>"
        )
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1, text)
        self.assertEqual(calls[0].name, "edit_file")
        self.assertIn(
            'snapshot.putString("phs_name", resolveVillagerName(villager));',
            calls[0].args["old"],
        )
        self.assertIn('snapshot.put("phs_relations"', calls[0].args["new"])
        self.assertEqual(strip_tool_blocks(text), "先加工具方法并改 snapshot。")

    def test_dsml_edit_file_with_literal_newlines(self) -> None:
        # 另一类坏格式：字符串里直接换行，没有写成 \n。
        text = (
            '<|DSML|calls><|DSML|invoke name="tool">'
            '{"tool":"edit_file","path":"src/A.java","old":"class A {\n'
            '    int x;\n}","new":"class A {\n    int y;\n}"}'
            "</|DSML|parameter></|DSML|invoke></|DSML|calls>"
        )
        calls = parse_tool_calls(text)
        self.assertEqual(len(calls), 1, text)
        self.assertIn("int x;", calls[0].args["old"])
        self.assertIn("int y;", calls[0].args["new"])
        self.assertEqual(strip_tool_blocks(text), "")

    def test_strip_tool_blocks(self) -> None:
        text = "说明\n\n```tool\n{\"tool\":\"list_files\"}\n```\n\n结束"
        cleaned = strip_tool_blocks(text)
        self.assertNotIn("list_files", cleaned)
        self.assertIn("说明", cleaned)
        self.assertIn("结束", cleaned)

    def test_extract_from_fence_info(self) -> None:
        text = """### 文件：`src/main/java/com/example/ExampleMod.java`

```java src/main/java/com/example/ExampleMod.java
package com.example;
```
"""
        files = extract_file_blocks(text)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path, "src/main/java/com/example/ExampleMod.java")
        self.assertIn("package com.example", files[0].content)

    def test_extract_from_heading(self) -> None:
        text = """### src/main/resources/fabric.mod.json

```json
{"id": "example"}
```
"""
        files = extract_file_blocks(text)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path, "src/main/resources/fabric.mod.json")

    def test_ignore_unnamed_and_unsafe(self) -> None:
        text = """```java
class A {}
```

### ../outside/B.java

```java
class B {}
```
"""
        self.assertEqual(extract_file_blocks(text), [])

    def test_deduplicate(self) -> None:
        text = """### src/A.java

```java
class A {}
```

### src/A.java

```java
class A { int x; }
```
"""
        files = extract_file_blocks(text)
        self.assertEqual(len(files), 1)
        self.assertIn("int x", files[0].content)

    def test_ignore_tool_fence(self) -> None:
        text = """```tool
{"tool":"read_file","path":"src/A.java"}
```
"""
        self.assertEqual(extract_file_blocks(text), [])

    def test_pending_preview(self) -> None:
        item = PendingFile(path="A.java", content="x" * 200)
        self.assertTrue(item.preview(10).endswith("…"))


if __name__ == "__main__":
    unittest.main()
