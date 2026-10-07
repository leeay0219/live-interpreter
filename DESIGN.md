---
version: alpha
name: Pre-show
description: Live English-Korean interpretation captions. A navy studio with a clear product header, audio and Start together, compact optional settings, and a still caption preview.
colors:
  canvas: "#0b1622"
  panel: "#101d2c"
  line: "#1f2d3d"
  line-input: "#3a4a5c"
  on-surface: "#ffffff"
  on-surface-muted: "#9aabbf"
  on-surface-faint: "#8293a7"
  primary: "#ff9900"
  primary-hover: "#ffad33"
  on-primary: "#0b1622"
  brand: "#ff9900"
  on-brand: "#0b1622"
  focus: "#ff9900"
  control: "#1b2939"
  control-hover: "#243446"
  selected: "#ff9900"
  live: "#3ecf8e"
  warning: "#f5c451"
  error: "#ff7a7a"
  source-line-on-paper: "#b9a8f5"
  chrome: "#232f3e"
  chrome-raised: "#192534"
  chrome-line: "#414d5c"
  on-chrome: "#e9ebed"
  on-chrome-muted: "#9ba7b6"
  chrome-accent: "#539fe5"
  chrome-live: "#29ad32"
  chrome-warning: "#f2cd54"
  chrome-error: "#ff7a7a"
  phone-canvas: "#0f1b2a"
  on-air: "#ff5d64"
  stage: "#000000"
  caption-bar: "#05090d"
  caption-now: "#ffffff"
  caption-prev: "#c8d2dc"
  caption-old: "#8f9eae"
  source-line: "#b9a8f5"
  paper: "#ffffff"
  ink: "#0b1622"
  ink-prev: "#3a4a5c"
  ink-old: "#687586"
typography:
  app-name:
    fontFamily: Amazon Ember
    fontSize: 26px
    fontWeight: 700
    lineHeight: 1.2
  input-title:
    fontFamily: Amazon Ember
    fontSize: 18px
    fontWeight: 600
    lineHeight: 1.4
  sheet-title:
    fontFamily: Amazon Ember
    fontSize: 17px
    fontWeight: 700
    lineHeight: 1.3
  label-md:
    fontFamily: Amazon Ember
    fontSize: 14px
    fontWeight: 600
    lineHeight: 1.4
  body-md:
    fontFamily: Amazon Ember
    fontSize: 14px
    fontWeight: 400
    lineHeight: 1.45
  body-sm:
    fontFamily: Amazon Ember
    fontSize: 12px
    fontWeight: 400
    lineHeight: 1.4
  button:
    fontFamily: Amazon Ember
    fontSize: 13px
    fontWeight: 600
    lineHeight: 1.2
  start:
    fontFamily: Amazon Ember
    fontSize: 17px
    fontWeight: 700
    lineHeight: 1.2
  caption-line:
    fontFamily: Amazon Ember
    fontSize: 42px
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: -0.3px
  caption-source:
    fontFamily: Amazon Ember
    fontSize: 24px
    fontWeight: 400
    lineHeight: 1.3
  phone-caption:
    fontFamily: Amazon Ember
    fontSize: 19px
    fontWeight: 400
    lineHeight: 1.6
rounded:
  sm: 6px
  md: 8px
  lg: 12px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 12px
  lg: 16px
  xl: 24px
  side-width: 348px
  row-height: 48px
  workspace-gap: 40px
components:
  side-panel:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.on-surface}"
    width: "{spacing.side-width}"
  side-row:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.on-surface}"
    typography: "{typography.label-md}"
    height: "{spacing.row-height}"
  side-row-value:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.on-surface-muted}"
    typography: "{typography.body-md}"
  preview-area:
    backgroundColor: "{colors.canvas}"
    textColor: "{colors.on-surface}"
  sheet:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.on-surface}"
    typography: "{typography.sheet-title}"
    rounded: "{rounded.lg}"
  button-start:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    typography: "{typography.start}"
    rounded: "{rounded.md}"
    height: 52px
  button-start-hover:
    backgroundColor: "{colors.primary-hover}"
  button:
    backgroundColor: "{colors.control}"
    textColor: "{colors.on-surface}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
    padding: 6px 12px
  button-hover:
    backgroundColor: "{colors.control-hover}"
  segmented-track:
    backgroundColor: "{colors.control}"
    textColor: "{colors.on-surface-muted}"
    typography: "{typography.button}"
    rounded: "{rounded.md}"
  segmented-selected:
    backgroundColor: "{colors.selected}"
    textColor: "{colors.on-primary}"
    typography: "{typography.button}"
    rounded: "{rounded.sm}"
  input-field:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.on-surface}"
    typography: "{typography.body-md}"
    rounded: "{rounded.md}"
    height: 36px
    padding: 0 12px
  input-outline:
    backgroundColor: "{colors.line-input}"
    height: 1px
  focus-ring:
    backgroundColor: "{colors.focus}"
    height: 2px
  hairline:
    backgroundColor: "{colors.line}"
    height: 1px
  hint-text:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.on-surface-faint}"
    typography: "{typography.body-sm}"
  brand-mark:
    backgroundColor: "{colors.brand}"
    textColor: "{colors.on-brand}"
    rounded: "{rounded.sm}"
    size: 34px
  status-live:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.live}"
    typography: "{typography.body-sm}"
  status-warning:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.warning}"
    typography: "{typography.body-sm}"
  status-error:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.error}"
    typography: "{typography.body-sm}"
  misheard-word:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.source-line-on-paper}"
    typography: "{typography.body-md}"
  control-bar:
    backgroundColor: "{colors.chrome}"
    textColor: "{colors.on-chrome}"
    rounded: "{rounded.full}"
  control-bar-active:
    backgroundColor: "{colors.chrome}"
    textColor: "{colors.chrome-accent}"
  chrome-panel:
    backgroundColor: "{colors.chrome-raised}"
    textColor: "{colors.on-chrome}"
    rounded: "{rounded.lg}"
  chrome-panel-muted:
    backgroundColor: "{colors.chrome-raised}"
    textColor: "{colors.on-chrome-muted}"
  chrome-divider:
    backgroundColor: "{colors.chrome-line}"
    height: 1px
  chrome-status-live:
    backgroundColor: "{colors.chrome-live}"
    rounded: "{rounded.full}"
    size: 8px
  chrome-status-warning:
    backgroundColor: "{colors.chrome-warning}"
    rounded: "{rounded.full}"
    size: 8px
  chrome-status-error:
    backgroundColor: "{colors.chrome-raised}"
    textColor: "{colors.chrome-error}"
  lamp-on-air:
    backgroundColor: "{colors.on-air}"
    rounded: "{rounded.full}"
    size: 6px
  live-screen:
    backgroundColor: "{colors.stage}"
    textColor: "{colors.caption-now}"
  caption-bar:
    backgroundColor: "{colors.caption-bar}"
    textColor: "{colors.caption-now}"
    typography: "{typography.caption-line}"
  caption-line-prev:
    backgroundColor: "{colors.caption-bar}"
    textColor: "{colors.caption-prev}"
    typography: "{typography.caption-line}"
  caption-line-old:
    backgroundColor: "{colors.caption-bar}"
    textColor: "{colors.caption-old}"
    typography: "{typography.caption-line}"
  source-line:
    backgroundColor: "{colors.caption-bar}"
    textColor: "{colors.source-line}"
    typography: "{typography.caption-source}"
  caption-bar-light:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    typography: "{typography.caption-line}"
  caption-line-prev-light:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink-prev}"
    typography: "{typography.caption-line}"
  caption-line-old-light:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink-old}"
    typography: "{typography.caption-line}"
  phone-view:
    backgroundColor: "{colors.phone-canvas}"
    textColor: "{colors.caption-now}"
    typography: "{typography.phone-caption}"
---

## Overview

One window to prepare a live interpretation session. A full-width product header establishes the application. A compact panel on the left groups slides, the microphone picker, its sound bar and Start in reading order. Optional settings sit below it. The preview on the right uses the real caption style, size and background, with short, still labels that cannot be mistaken for a conversation.

Uploading a document automatically builds translation context. Its status sits with the file; 분석 보기 opens a brief with source page references. Manual speaker and term corrections are optional disclosures inside 고급 설정, not steps on the main screen. Existing corrections remain visible in the advanced row's summary. You glance at the preview, check the sound bar moves, press the one orange button, and you are on.

## Colors

AWS navy and orange, nothing else.
- **Navy** {colors.canvas} is the window; **Panel** {colors.panel} groups the main inputs, preview controls and slide-over panels, one step lighter.
- **Orange** {colors.primary} is the big 시작 button, the app mark, the selected segment and selected skill, and the focus ring. It is never a glow or a gradient.
- Grey keys {colors.control} for every other button. Hairlines {colors.line} divide functional groups; avoid a full-width rule around every setting.
- Status green {colors.live}, warning {colors.warning} and error {colors.error} appear as a small dot and short text.
- Misheard words are violet {colors.source-line-on-paper}, the same meaning as the source line in captions.
- The live screen, its control bar ({colors.chrome}) and panels ({colors.chrome-raised}), and the phone view ({colors.phone-canvas}) stay dark.

## Typography

Amazon Ember, then the device's Korean UI face. Deliberate hierarchy: product title 26px bold, audio heading 18px semibold, panel titles and Start 17px, optional setting labels and inputs 14px, secondary text 12px. The larger product title and audio heading are intentional; do not flatten everything to the same small size.

Copy: labels are nouns (발표 자료, 음성 입력, 발표자). No descriptions under labels. Hints only where the operator must act (screen-share instructions, a test file warning, a microphone error). Korean UI text has no middle dots, arrows or dashes.

## Layout

An 80px product header spans the window. Below it, a centered workspace (maximum 1440px including padding) has settings {spacing.side-width} wide on the left, a {spacing.workspace-gap} gutter, and the preview on the right. Both columns start at the same top edge. The main input panel contains 발표 자료 with automatic analysis status, 음성 입력, the sound bar and a full-width Start button directly below the input. A single 고급 설정 row sits below. When interpretation ends, the same columns show the completed session and its documents.

The preview has a visible 미리보기 heading outside its 16:9 stage. Its size, background and source-line controls share a footer within the frame. Keep all controls within reach at 1280x800; allow scrolling when recording controls, errors or larger text require it. Below 900px the preview follows the settings. Below 480px the preview controls wrap by function, with touch targets at least 44px.

Panels open over the preview from the right, one at a time, and close with 닫기 or Esc. Focus moves into the panel and returns to its trigger on close. On narrow screens panels are fixed to the viewport so opening a setting never puts its panel below the fold.

## Elevation & Depth

Flat. The main input panel and preview footer are one tonal step lighter than the canvas. Only overlays have a shadow: slide-over panels, the live control bar, the share menu and the log drawer.

## Shapes

{rounded.md} for buttons and inputs, {rounded.lg} for the preview and panels, full pills only for the live control bar.

## Components

- **시작**: full-width orange button directly below the sound check, the brightest control on the screen.
- **Buttons**: grey fill, light text, no outline; hover a shade lighter. Destructive actions are the same grey buttons.
- **Rows**: label on the left, current value and a small chevron on the right; the whole row is the click target. Hover and the open panel's trigger have a quiet filled background.
- **Segmented control**: grey track; the selected segment is orange with dark text.
- **Sound bar**: thin green bar with a dot and one word (들어오고 있습니다, 조용합니다, or the error).
- **Caption bar**: three rolling lines, newest at the bottom, older lines dimmer.
- **Preview**: three still labels (지난 자막, 이전 자막, 한국어 자막 예시) and Original speech as the source label. No timer, fictional speech, explanatory paragraphs or LIVE badge.
- **Document context**: short progress/status beside the uploaded file; analysis details in a panel with a brief and source page references. Label extracted people 자료에 나온 인물, never assume they are the live speakers. Keep text-extraction limitations in the details and actionable errors at the upload control.

## Do's and Don'ts

- Do fit setup in one window without scrolling at 1280x800.
- Do keep the microphone and Start together, including when error text or recording controls expand.
- Do keep manual corrections in 고급 설정 and show a compact summary when any are active.
- Don't put descriptions under labels or sentences that restate the screen.
- Don't use outlined colored buttons, step numbers, check marks, badges, glows, gradients or glass.
- Use orange for the current primary action (통역 시작 during setup, 정리하기 after ending), the app mark, selected states and focus.
- Don't animate the preview or label it LIVE: moving sample captions look like real captions coming from the microphone.
- Don't animate captions beyond the short rise of a new line.


## Session and material workflows (2026-10-05)

The main input panel ends with the orange 통역 시작 button. A secondary 통역 테스트 button beside the 음성 입력 heading opens a private 12-second rehearsal in a sheet. Keep the uploaded file thumbnail, name and page count together, followed by analysis status and one left-aligned row of 분석 보기, 바꾸기 and 삭제 actions. 참고 자료 is a separate full-width row below, with its count on the right. Reference pages are never projected.

Upload failures appear at the document control in Korean, not beside Start. Disable repeated upload/change/delete actions while a file is being read. A connection failure also shows a short server notice; never expose the browser's raw “Failed to fetch” as the operator's only guidance.

Analysis entries have 근거와 보정. Open the cited page in an evidence sheet, edit the spelling in place, exclude it or restore the original. Distinguish image-derived spelling from native-text evidence without invented confidence percentages. Partial analysis shows completed coverage and failed pages. Keep the page image inside the sheet width on mobile.

Live controls add 잠시 멈춤 / 이어서 하기 and 질의응답 / 발표로 돌아가기. Waiting for the last captions disables Resume until the server acknowledges Pause. End opens a completed-session view and releases the microphone. Hide input, rehearsal, settings and caption preview. The left column shows 통역을 마쳤습니다, the caption count and duration, original-record downloads, a collapsed 기록 관리 disclosure and a secondary 새 세션 준비 button. The right column is a large inline 정리 문서 region with generation choices, output actions and the full preview. On mobile the document follows the session record in normal page flow.

Until the server confirms the last captions are finished, show 통역을 마무리하고 있습니다 and disable generation and 새 세션 준비. Selecting 새 세션 준비 clears materials, corrections and conversation, then returns to setup; pause and reconnect preserve them. Device and display preferences persist in the browser; manual session content does not persist in localStorage.

The status line normally says 통역 중. Show actionable states for missing input, low audio, recognition delay, translation delay, disconnected hardware and reconnecting. Do not use confidence badges or extra numbered steps. The private rehearsal sheet displays only speech actually provided for that rehearsal and is stopped when the sheet closes.

## Exported documents

HTML exports are reading documents: white paper, navy text, a thin orange top rule, clear headings and restrained tables. Use natural paragraphs without slogans, decorative cards or oversized opening sections. Keep Korean words together when wrapping. Embed the stylesheet in the file; no external fonts, scripts, images or automatic network requests. Long documents have a compact expandable contents list. Print styles use A4 margins and hide navigation.

The write-up actions are 문서 열기, HTML 받기, Word 받기 and Markdown 받기. Raw record downloads also include HTML and preserve the original transcript and displayed captions. Generated summaries and meeting notes use the same plain editorial rules in every format. Exact quotations, code and formulas keep their source punctuation.

Compose the result by subject, without processing-window or slide headings. Omit empty decisions and action sections. Keep recognition corrections and source references in a collapsed 검토 내역 disclosure below the result, with a separate JSON download. Only uncertainty that changes the reader's understanding or next action belongs in the reading document.
