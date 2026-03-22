// work around opencode bug: only one hand-installed plugin loads per session.
// static re-exports let opencode discover both init functions from a single import.
export { EvolvePlugin } from "opencode-evolve"
export { BridgePlugin } from "opencode-bridge"
