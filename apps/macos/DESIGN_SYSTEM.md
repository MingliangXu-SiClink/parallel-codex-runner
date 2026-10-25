# Parallel Codex macOS Design System

The macOS client is a conversation application, not a runner dashboard. It uses
native SwiftUI and AppKit behavior, with the installed ChatGPT app as a visual
reference rather than a source-code dependency.

## Product Frame

The window has three stable regions:

1. **Sidebar**: recent conversations and a flat Workspace list; computer choice
   is a compact popover in the product header.
2. **Conversation canvas**: the accepted history and the current turn.
3. **Result inspector**: optional Agent details, never required for reading the
   conversation.

The native window toolbar owns the conversation title and global actions. Do
not add a second in-content title bar.

## Layout

- Use the 4-point spacing grid in `ParallelCodexVisualStyle.Spacing`.
- Keep the transcript in a balanced, centered reading column on wide windows;
  individual message content keeps its role-appropriate alignment inside it.
- Keep the composer at a stable maximum width and centered with the transcript.
- Use the design-system sidebar and inspector widths. Local views must not add
  their own breakpoints.
- Do not divide the sidebar into independently resizable list panes or build a
  Computer -> Workspace -> Conversation tree. Keep recent conversations and
  Workspaces in one scroll surface, with conversations visually primary.
- Keep icon controls at the shared compact or standard control size so loading,
  disabled, and active states do not shift surrounding content.

## Visual Roles

- **Canvas** is quiet and unframed.
- **Sidebar** is visually distinct from the canvas, but not a stack of cards.
- **Selection** is a neutral tonal surface. Accent color communicates action,
  not ordinary navigation state.
- **User messages** use a compact trailing bubble.
- **Agent replies** are direct reading content without a containing card.
- **Agent reply text** always uses the primary text color, including live
  partial replies. Only reasoning and tool activity are visually secondary.
- **Inline code** is distinguished by monospaced type, not repeated gray
  rectangles inside prose.
- **Code blocks** stay on the conversation canvas and use spacing plus a thin
  accent rule; never paint a wide gray band or separate backgrounds per line.
- **Commands and tool activity** may use a subtle tool surface because they are
  secondary, structured information.
- **Warnings and failures** use semantic system colors only when action is
  required.

## Typography

- Use the system font and semantic SwiftUI/AppKit text styles.
- Conversation content is the dominant text layer.
- Toolbar titles and sidebar labels are compact and semibold.
- Metadata is secondary and must remain on one line when possible.
- Do not use display-sized headings inside tool, sidebar, or inspector regions.

## Interaction

- Switching Agent candidates replaces only the current candidate response.
- Never clear visible content before replacement content is ready.
- Validate and hydrate a destination computer or Workspace before committing
  the visible context; navigation failure preserves the previous context.
- Expanding work, opening an inspector, and refreshing live output must preserve
  scroll position.
- User-driven navigation never performs an implicit finalize.
- Destructive or branch-changing actions require explicit language; routine
  navigation and refresh actions do not need confirmation.
- Respect Reduce Motion. Transitions are short and may not move the transcript
  unless the user requested navigation.

## Components

Use the shared components in `ParallelCodexVisualStyle.swift`:

- `ParallelCodexStatusDot`
- `ParallelCodexIconButtonStyle`
- `parallelCodexSidebarRow(selected:activeParent:)`

Add a shared component only when at least two product surfaces need the same
behavior. Keep one-off presentation local to its owning feature.

## Review Checklist

- Verify light and dark appearances.
- Verify the default and minimum supported window sizes.
- Verify text scaling and long Chinese/English labels.
- Verify loading and live-update states do not resize stable controls.
- Verify Agent switching, work expansion, and inspector presentation do not
  flash blank content or change the user's scroll position.
- Verify switching to an offline computer leaves the current conversation
  visible and reports the failure without a whole-window overlay.
- Verify the signed installed app, not only the development build.

## Reference Discipline

The installed ChatGPT app is a behavioral and visual reference for navigation,
reading rhythm, and disclosure. It is not a source dependency. Runtime state
handling follows the same broad principles demonstrated by mature open-source
clients: immutable committed transcript history plus a replaceable live tail,
local cached data as the first paint, throttled presentation updates, and
feature-owned views rather than one dashboard-shaped screen.
