---
name: release-checklist
description: 出版前的檢查：測試、文件、版本號、git 狀態。使用者說要發版、要 commit 一版時用。
tools: run_tests, run_shell, read_file
---

# 出版前檢查

照 [`checklist.md`](checklist.md) 一項一項來，**每一項都要有實際證據**，
不要憑印象打勾。

## 步驟

1. `run_tests` —— 全綠才往下走。有紅的就停在這裡回報，不要「順便修一下」。
2. `run_shell` 跑 `git status --porcelain`，確認沒有預期外的檔案。
   有沒看過的檔案就列出來問使用者。
3. 讀 README 與其他文件，確認這次改的東西有寫進去。
   **文件沒更新等於功能沒做完。**
4. 把 checklist 的結果整理成三到五行回報，讓使用者決定要不要 commit。

## 判斷準則

- **不要自己 commit。** 這個 skill 只做檢查，按下去的是人。
- 「測試沒有涵蓋到這次的改動」也算沒過，要講出來。
- 版本號沒有規則就不要自己編一個，問使用者。

## 什麼時候不要用這個

只是想跑一下測試的時候——那用 `run-pytest` 就好，這份會多做四件事。
