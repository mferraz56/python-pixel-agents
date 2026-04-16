import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as vscode from 'vscode';

import type { HookEvent } from '../server/src/hookEventHandler.js';
import { HookEventHandler } from '../server/src/hookEventHandler.js';
import {
  installHooks,
  uninstallHooks,
} from '../server/src/providers/hook/claude/claudeHookInstaller.js';
import { claudeProvider, copyHookScript } from '../server/src/providers/index.js';
import { PixelAgentsServer } from '../server/src/server.js';
import {
  getProjectDirPath,
  launchNewTerminal,
  persistAgents,
  removeAgent,
  restoreAgents,
  sendCurrentAgentStatuses,
  sendExistingAgents,
  sendLayout,
} from './agentManager.js';
import type {
  LoadedAssets,
  LoadedCharacterSprites,
  LoadedFloorTiles,
  LoadedWallTiles,
} from './assetLoader.js';
import {
  loadCharacterSprites,
  loadDefaultLayout,
  loadExternalCharacterSprites,
  loadFloorTiles,
  loadFurnitureAssets,
  loadWallTiles,
  mergeCharacterSprites,
  mergeLoadedAssets,
  sendAssetsToWebview,
  sendCharacterSpritesToWebview,
  sendFloorTilesToWebview,
  sendWallTilesToWebview,
} from './assetLoader.js';
import { readConfig, writeConfig } from './configPersistence.js';
import {
  GLOBAL_KEY_ALWAYS_SHOW_LABELS,
  GLOBAL_KEY_HOOKS_ENABLED,
  GLOBAL_KEY_HOOKS_INFO_SHOWN,
  GLOBAL_KEY_LAST_SEEN_VERSION,
  GLOBAL_KEY_SOUND_ENABLED,
  GLOBAL_KEY_WATCH_ALL_SESSIONS,
  LAYOUT_REVISION_KEY,
  WORKSPACE_KEY_AGENT_SEATS,
} from './constants.js';
import {
  adoptExternalSessionFromHook,
  dismissedJsonlFiles,
  ensureProjectScan,
  isTrackedProjectDir,
  reassignAgentToFile,
  scanForTeammateFiles,
  seededMtimes,
  setTeammateRemovalCallback,
  setTeamProvider,
  startExternalSessionScanning,
  startStaleExternalAgentCheck,
} from './fileWatcher.js';
import type { LayoutWatcher } from './layoutPersistence.js';
import { readLayoutFromFile, watchLayoutFile, writeLayoutToFile } from './layoutPersistence.js';
import {
  sendMessage,
  type MessageSender,
  type SettingsLoadedMessage,
  type ViewerMessage,
} from '../shared/messages.js';
import { setHookProvider } from './transcriptParser.js';
import type { AgentState } from './types.js';
import { ViewerMessageRelay } from './viewerMessageRelay.js';

export class PixelAgentsViewProvider implements vscode.WebviewViewProvider {
  nextAgentId = { current: 1 };
  nextTerminalIndex = { current: 1 };
  agents = new Map<number, AgentState>();
  webviewView: vscode.WebviewView | undefined;

  // Per-agent timers
  fileWatchers = new Map<number, fs.FSWatcher>();
  pollingTimers = new Map<number, ReturnType<typeof setInterval>>();
  waitingTimers = new Map<number, ReturnType<typeof setTimeout>>();
  jsonlPollTimers = new Map<number, ReturnType<typeof setInterval>>();
  permissionTimers = new Map<number, ReturnType<typeof setTimeout>>();

  // /clear detection: project-level scan for new JSONL files
  activeAgentId = { current: null as number | null };
  knownJsonlFiles = new Set<string>();
  projectScanTimer = { current: null as ReturnType<typeof setInterval> | null };

  // External session detection (VS Code extension panel, etc.)
  externalScanTimer: ReturnType<typeof setInterval> | null = null;
  staleCheckTimer: ReturnType<typeof setInterval> | null = null;

  // Global session scanning (opt-in "Watch All Sessions" toggle)
  watchAllSessions = { current: false };
  // Hooks enabled state (mutable ref for passing to scanners)
  hooksEnabled = { current: true };
  globalDismissedFiles = new Set<string>();

  // Bundled default layout (loaded from assets/default-layout.json)
  defaultLayout: Record<string, unknown> | null = null;

  // Root path of bundled assets (set once on first load)
  private assetsRoot: string | null = null;

  // Cross-window layout sync
  layoutWatcher: LayoutWatcher | null = null;

  // Pixel Agents Server (hook event reception)
  private pixelAgentsServer: PixelAgentsServer | null = null;
  // ServerConfig is not stored as a field; use this.pixelAgentsServer?.getConfig() if needed.
  private hookEventHandler: HookEventHandler | null = null;
  private readonly viewerMessageRelay = new ViewerMessageRelay(
    () => this.webview,
    (message) => this.pixelAgentsServer?.broadcastViewerMessage(message),
  );
  private serverReadyPromise: Promise<void> | null = null;
  private runtimeStarted = false;
  private runtimeStartPromise: Promise<void> | null = null;
  private loadedAssets: LoadedAssets | null = null;
  private loadedCharacterSprites: LoadedCharacterSprites | null = null;
  private loadedFloorTiles: LoadedFloorTiles | null = null;
  private loadedWallTiles: LoadedWallTiles | null = null;

  constructor(private readonly context: vscode.ExtensionContext) {
    this.initHooks();
  }

  private get extensionUri(): vscode.Uri {
    return this.context.extensionUri;
  }

  private get webview(): vscode.Webview | undefined {
    return this.webviewView?.webview;
  }

  private persistAgents = (): void => {
    persistAgents(this.agents, this.context);
  };

  private buildSettingsLoadedMessage(): SettingsLoadedMessage {
    const soundEnabled = this.context.globalState.get<boolean>(GLOBAL_KEY_SOUND_ENABLED, true);
    const lastSeenVersion = this.context.globalState.get<string>(GLOBAL_KEY_LAST_SEEN_VERSION, '');
    const extensionVersion =
      (this.context.extension.packageJSON as { version?: string }).version ?? '';
    const watchAllSessions = this.context.globalState.get<boolean>(
      GLOBAL_KEY_WATCH_ALL_SESSIONS,
      false,
    );
    const alwaysShowLabels = this.context.globalState.get<boolean>(
      GLOBAL_KEY_ALWAYS_SHOW_LABELS,
      false,
    );
    const hooksEnabled = this.context.globalState.get<boolean>(GLOBAL_KEY_HOOKS_ENABLED, true);
    const hooksInfoShown = this.context.globalState.get<boolean>(GLOBAL_KEY_HOOKS_INFO_SHOWN, false);
    const config = readConfig();
    return {
      type: 'settingsLoaded',
      soundEnabled,
      lastSeenVersion,
      extensionVersion,
      watchAllSessions,
      alwaysShowLabels,
      hooksEnabled,
      hooksInfoShown,
      externalAssetDirectories: config.externalAssetDirectories,
      remoteViewerUrl: this.getRemoteViewerUrl(),
    };
  }

  private sendLocalBootstrap(sender: MessageSender): void {
    sendMessage(sender, this.buildSettingsLoadedMessage());

    const wsFolders = vscode.workspace.workspaceFolders;
    if (wsFolders && wsFolders.length > 1) {
      sendMessage(sender, {
        type: 'workspaceFolders',
        folders: wsFolders.map((f) => ({ name: f.name, path: f.uri.fsPath })),
      });
    }

    sendExistingAgents(this.agents, this.context, sender);

    if (this.loadedCharacterSprites) {
      sendCharacterSpritesToWebview(sender, this.loadedCharacterSprites);
    }
    if (this.loadedFloorTiles) {
      sendFloorTilesToWebview(sender, this.loadedFloorTiles);
    }
    if (this.loadedWallTiles) {
      sendWallTilesToWebview(sender, this.loadedWallTiles);
    }
    if (this.loadedAssets) {
      sendAssetsToWebview(sender, this.loadedAssets);
    }

    sendLayout(this.context, sender, this.defaultLayout);
    sendCurrentAgentStatuses(this.agents, sender);
    if (this.activeAgentId.current !== null) {
      sendMessage(sender, { type: 'agentSelected', id: this.activeAgentId.current });
    }
  }

  private async buildViewerBootstrapMessages(): Promise<ViewerMessage[]> {
    await this.ensureRuntimeStarted();
    const messages: ViewerMessage[] = [];
    this.sendLocalBootstrap({
      postMessage(message) {
        messages.push(message);
      },
    });
    return messages;
  }

  private async ensureRuntimeStarted(): Promise<void> {
    if (this.runtimeStarted) return;
    if (this.runtimeStartPromise) {
      await this.runtimeStartPromise;
      return;
    }

    this.runtimeStartPromise = this.startRuntime();
    try {
      await this.runtimeStartPromise;
      this.runtimeStarted = true;
    } finally {
      this.runtimeStartPromise = null;
    }
  }

  private async startRuntime(): Promise<void> {
    restoreAgents(
      this.context,
      this.nextAgentId,
      this.nextTerminalIndex,
      this.agents,
      this.knownJsonlFiles,
      this.fileWatchers,
      this.pollingTimers,
      this.waitingTimers,
      this.permissionTimers,
      this.jsonlPollTimers,
      this.projectScanTimer,
      this.activeAgentId,
      this.viewerMessageRelay,
      this.persistAgents,
    );

    for (const agent of this.agents.values()) {
      this.registerAgentHook(agent);
    }

    const watchAllSessions = this.context.globalState.get<boolean>(
      GLOBAL_KEY_WATCH_ALL_SESSIONS,
      false,
    );
    this.watchAllSessions.current = watchAllSessions;
    this.hooksEnabled.current = this.context.globalState.get<boolean>(GLOBAL_KEY_HOOKS_ENABLED, true);

    const projectDir = getProjectDirPath();
    ensureProjectScan(
      projectDir,
      this.knownJsonlFiles,
      this.projectScanTimer,
      this.activeAgentId,
      this.nextAgentId,
      this.agents,
      this.fileWatchers,
      this.pollingTimers,
      this.waitingTimers,
      this.permissionTimers,
      this.viewerMessageRelay,
      this.persistAgents,
      (agent) => this.registerAgentHook(agent),
      this.hooksEnabled,
    );

    const wsFolders = vscode.workspace.workspaceFolders;
    if (wsFolders && wsFolders.length > 1) {
      for (const folder of wsFolders) {
        const folderProjectDir = getProjectDirPath(folder.uri.fsPath);
        if (folderProjectDir && folderProjectDir !== projectDir) {
          ensureProjectScan(
            folderProjectDir,
            this.knownJsonlFiles,
            this.projectScanTimer,
            this.activeAgentId,
            this.nextAgentId,
            this.agents,
            this.fileWatchers,
            this.pollingTimers,
            this.waitingTimers,
            this.permissionTimers,
            this.viewerMessageRelay,
            this.persistAgents,
            undefined,
            this.hooksEnabled,
          );
        }
      }
    }

    if (!this.externalScanTimer) {
      this.externalScanTimer = startExternalSessionScanning(
        projectDir,
        this.knownJsonlFiles,
        this.nextAgentId,
        this.agents,
        this.fileWatchers,
        this.pollingTimers,
        this.waitingTimers,
        this.permissionTimers,
        this.jsonlPollTimers,
        this.viewerMessageRelay,
        this.persistAgents,
        this.watchAllSessions,
        this.hooksEnabled,
      );
    }

    if (!this.staleCheckTimer) {
      this.staleCheckTimer = startStaleExternalAgentCheck(
        this.agents,
        this.knownJsonlFiles,
        this.fileWatchers,
        this.pollingTimers,
        this.waitingTimers,
        this.permissionTimers,
        this.jsonlPollTimers,
        this.viewerMessageRelay,
        this.persistAgents,
        this.hooksEnabled,
      );
    }

    await this.loadRuntimeAssets();
    this.startLayoutWatcher();
  }

  private initHooks(): void {
    this.hookEventHandler = new HookEventHandler(
      this.agents,
      this.waitingTimers,
      this.permissionTimers,
      () => this.viewerMessageRelay,
      claudeProvider,
      this.watchAllSessions,
    );

    // Register Claude's team provider (if present on the hook provider) with the file
    // watcher module + transcriptParser, plus the teammate removal callback.
    if (claudeProvider.team) {
      setTeamProvider(claudeProvider.team);
    }
    setHookProvider(claudeProvider);
    setTeammateRemovalCallback((id) => this.removeTeammate(id, 'team-config'));

    this.hookEventHandler.setLifecycleCallbacks({
      onExternalSessionDetected: (sessionId, transcriptPath, cwd) => {
        // Workspace filtering: only adopt if in a tracked project dir or Watch All Sessions is ON
        const projectDir = transcriptPath ? path.dirname(transcriptPath) : cwd;
        if (!isTrackedProjectDir(projectDir) && !this.watchAllSessions.current) {
          return; // Not our workspace and Watch All is OFF, ignore
        }
        adoptExternalSessionFromHook(
          sessionId,
          transcriptPath,
          cwd,
          this.knownJsonlFiles,
          this.nextAgentId,
          this.agents,
          this.fileWatchers,
          this.pollingTimers,
          this.waitingTimers,
          this.permissionTimers,
          this.viewerMessageRelay,
          this.persistAgents,
          (agent) => this.registerAgentHook(agent),
        );
      },
      onSessionClear: (agentId, newSessionId, newTranscriptPath) => {
        if (newTranscriptPath) {
          this.knownJsonlFiles.add(newTranscriptPath);
          reassignAgentToFile(
            agentId,
            newTranscriptPath,
            this.agents,
            this.fileWatchers,
            this.pollingTimers,
            this.waitingTimers,
            this.permissionTimers,
            this.viewerMessageRelay,
            this.persistAgents,
          );
        }
        // Update session mapping for future hook events
        const agent = this.agents.get(agentId);
        if (agent) {
          this.unregisterAgentHook(agent);
          agent.sessionId = newSessionId;
          this.registerAgentHook(agent);
        }
      },
      onSessionResume: (transcriptPath) => {
        // Clear dismissals so --resume can re-adopt the file
        dismissedJsonlFiles.delete(transcriptPath);
        seededMtimes.delete(transcriptPath);
        this.knownJsonlFiles.delete(transcriptPath);
      },
      onTeammateDetected: (parentAgentId, sessionId, _agentType) => {
        const parentAgent = this.agents.get(parentAgentId);
        if (!parentAgent) return;
        scanForTeammateFiles(
          parentAgent.projectDir,
          sessionId,
          parentAgentId,
          this.nextAgentId,
          this.agents,
          this.fileWatchers,
          this.pollingTimers,
          this.waitingTimers,
          this.permissionTimers,
          this.viewerMessageRelay,
          this.persistAgents,
          (agent) => this.registerAgentHook(agent),
        );
      },
      onTeammateRemoved: (teammateAgentId) => {
        this.removeTeammate(teammateAgentId, 'hooks');
      },
      onSessionEnd: (agentId) => {
        const agent = this.agents.get(agentId);
        if (!agent) return;
        // Dismiss the file so heuristic scanners don't re-adopt it
        seededMtimes.delete(agent.jsonlFile);
        dismissedJsonlFiles.set(agent.jsonlFile, Date.now());
        // If this is a team lead, remove its teammates
        if (agent.isTeamLead) {
          this.removeTeammates(agentId);
        }
        // External agents: remove immediately (no terminal to keep alive)
        if (agent.isExternal) {
          this.unregisterAgentHook(agent);
          removeAgent(
            agentId,
            this.agents,
            this.fileWatchers,
            this.pollingTimers,
            this.waitingTimers,
            this.permissionTimers,
            this.jsonlPollTimers,
            this.persistAgents,
          );
          sendMessage(this.viewerMessageRelay, { type: 'agentClosed', id: agentId });
        }
      },
    });

    this.pixelAgentsServer = new PixelAgentsServer();
    this.pixelAgentsServer.setViewerRoot(path.join(this.context.extensionPath, 'dist', 'webview'));
    this.pixelAgentsServer.setViewerBootstrapCallback(() => this.buildViewerBootstrapMessages());
    this.pixelAgentsServer.onHookEvent((providerId, event) => {
      this.hookEventHandler?.handleEvent(providerId, event as HookEvent);
    });

    this.serverReadyPromise = this.pixelAgentsServer
      .start()
      .then((config) => {
        // Server always starts regardless of hooks-enabled state.
        // It's the foundation for WebSocket transport and health monitoring.
        // Only hook installation/script-copy is gated by the toggle.
        const hooksEnabled = this.context.globalState.get<boolean>(GLOBAL_KEY_HOOKS_ENABLED, true);
        this.hooksEnabled.current = hooksEnabled;
        if (hooksEnabled) {
          installHooks();
          copyHookScript(this.context.extensionPath);
        }
        console.log(`[Pixel Agents] Server: ready on port ${config.port}`);
        sendMessage(this.viewerMessageRelay, this.buildSettingsLoadedMessage());
      })
      .catch((e) => {
        console.error(`[Pixel Agents] Failed to start server: ${e}`);
      });
  }

  /** Remove all teammates of a lead agent */
  /** Remove a single teammate agent (used by both hook callback and team config polling). */
  private removeTeammate(teammateAgentId: number, source: string): void {
    const agent = this.agents.get(teammateAgentId);
    if (!agent) return;
    console.log(`[Pixel Agents] Removing teammate ${teammateAgentId} (source: ${source})`);
    dismissedJsonlFiles.set(agent.jsonlFile, Date.now());
    this.unregisterAgentHook(agent);
    removeAgent(
      teammateAgentId,
      this.agents,
      this.fileWatchers,
      this.pollingTimers,
      this.waitingTimers,
      this.permissionTimers,
      this.jsonlPollTimers,
      this.persistAgents,
    );
    sendMessage(this.viewerMessageRelay, { type: 'agentClosed', id: teammateAgentId });
  }

  private removeTeammates(leadId: number): void {
    const teammates: number[] = [];
    for (const [id, agent] of this.agents) {
      if (agent.leadAgentId === leadId) {
        teammates.push(id);
      }
    }
    for (const id of teammates) {
      const agent = this.agents.get(id);
      if (agent) {
        console.log(`[Pixel Agents] Removing teammate ${id} (lead ${leadId} closed)`);
        dismissedJsonlFiles.set(agent.jsonlFile, Date.now());
        this.unregisterAgentHook(agent);
        removeAgent(
          id,
          this.agents,
          this.fileWatchers,
          this.pollingTimers,
          this.waitingTimers,
          this.permissionTimers,
          this.jsonlPollTimers,
          this.persistAgents,
        );
        sendMessage(this.viewerMessageRelay, { type: 'agentClosed', id });
      }
    }
  }

  /** Register an agent with the hook event handler for session->agent mapping.
   *  hookDelivered is NOT set here. It is set only in hookEventHandler.handleEvent()
   *  when an actual hook event arrives, preserving heuristic fallback for agents
   *  where hooks aren't working (older Claude, hooks not installed, etc.) */
  registerAgentHook(agent: AgentState): void {
    this.hookEventHandler?.registerAgent(agent.sessionId, agent.id);
  }

  /** Unregister an agent from the hook event handler */
  unregisterAgentHook(agent: AgentState): void {
    this.hookEventHandler?.unregisterAgent(agent.sessionId);
  }

  resolveWebviewView(webviewView: vscode.WebviewView) {
    this.webviewView = webviewView;
    webviewView.webview.options = { enableScripts: true };
    webviewView.webview.html = getWebviewContent(webviewView.webview, this.extensionUri);

    webviewView.webview.onDidReceiveMessage(async (message) => {
      if (message.type === 'openClaude') {
        const prevAgentIds = new Set(this.agents.keys());
        await launchNewTerminal(
          this.nextAgentId,
          this.nextTerminalIndex,
          this.agents,
          this.activeAgentId,
          this.knownJsonlFiles,
          this.fileWatchers,
          this.pollingTimers,
          this.waitingTimers,
          this.permissionTimers,
          this.jsonlPollTimers,
          this.projectScanTimer,
          this.viewerMessageRelay,
          this.persistAgents,
          message.folderPath as string | undefined,
          message.bypassPermissions as boolean | undefined,
        );
        // Register newly created agent(s) with hook handler
        for (const [id, agent] of this.agents) {
          if (!prevAgentIds.has(id)) {
            this.registerAgentHook(agent);
          }
        }
      } else if (message.type === 'focusAgent') {
        const agent = this.agents.get(message.id);
        if (agent) {
          if (agent.terminalRef) {
            agent.terminalRef.show();
          } else if (agent.leadAgentId !== undefined) {
            // Teammate (tmux): focus the lead's terminal instead
            const lead = this.agents.get(agent.leadAgentId);
            if (lead?.terminalRef) {
              lead.terminalRef.show();
            }
          }
        }
      } else if (message.type === 'closeAgent') {
        const agent = this.agents.get(message.id);
        if (agent) {
          if (agent.terminalRef) {
            agent.terminalRef.dispose();
          } else {
            // External agent — remove from tracking and dismiss the file
            // so the external scanner doesn't re-adopt it
            dismissedJsonlFiles.set(agent.jsonlFile, Date.now());
            removeAgent(
              message.id,
              this.agents,
              this.fileWatchers,
              this.pollingTimers,
              this.waitingTimers,
              this.permissionTimers,
              this.jsonlPollTimers,
              this.persistAgents,
            );
            sendMessage(this.viewerMessageRelay, { type: 'agentClosed', id: message.id });
          }
        }
      } else if (message.type === 'saveAgentSeats') {
        // Store seat assignments in a separate key (never touched by persistAgents)
        console.log(`[Pixel Agents] State: saveAgentSeats:`, JSON.stringify(message.seats));
        this.context.workspaceState.update(WORKSPACE_KEY_AGENT_SEATS, message.seats);
      } else if (message.type === 'saveLayout') {
        this.layoutWatcher?.markOwnWrite();
        writeLayoutToFile(message.layout as Record<string, unknown>);
      } else if (message.type === 'setSoundEnabled') {
        this.context.globalState.update(GLOBAL_KEY_SOUND_ENABLED, message.enabled);
      } else if (message.type === 'setLastSeenVersion') {
        this.context.globalState.update(GLOBAL_KEY_LAST_SEEN_VERSION, message.version as string);
      } else if (message.type === 'setAlwaysShowLabels') {
        this.context.globalState.update(GLOBAL_KEY_ALWAYS_SHOW_LABELS, message.enabled);
      } else if (message.type === 'setHooksEnabled') {
        const enabled = message.enabled as boolean;
        this.context.globalState.update(GLOBAL_KEY_HOOKS_ENABLED, enabled);
        this.hooksEnabled.current = enabled;
        if (enabled) {
          installHooks();
          copyHookScript(this.context.extensionPath);
          console.log('[Pixel Agents] Hooks enabled by user');
        } else {
          uninstallHooks();
          console.log('[Pixel Agents] Hooks disabled by user');
        }
        sendMessage(this.viewerMessageRelay, this.buildSettingsLoadedMessage());
      } else if (message.type === 'setHooksInfoShown') {
        this.context.globalState.update(GLOBAL_KEY_HOOKS_INFO_SHOWN, true);
      } else if (message.type === 'setWatchAllSessions') {
        const enabled = message.enabled as boolean;
        this.context.globalState.update(GLOBAL_KEY_WATCH_ALL_SESSIONS, enabled);
        this.watchAllSessions.current = enabled;
        if (enabled) {
          // Clear only toggle-specific dismissals so global agents can be re-adopted
          for (const file of this.globalDismissedFiles) {
            dismissedJsonlFiles.delete(file);
          }
          this.globalDismissedFiles.clear();
        } else {
          // Remove all external agents not from the current workspace folders
          const workspaceDirs = new Set<string>();
          for (const folder of vscode.workspace.workspaceFolders ?? []) {
            const dir = getProjectDirPath(folder.uri.fsPath);
            if (dir) workspaceDirs.add(dir);
          }
          const toRemove: number[] = [];
          for (const [id, agent] of this.agents) {
            if (agent.isExternal && !workspaceDirs.has(agent.projectDir)) {
              toRemove.push(id);
            }
          }
          for (const id of toRemove) {
            const agent = this.agents.get(id);
            if (agent) {
              dismissedJsonlFiles.set(agent.jsonlFile, Date.now());
              this.globalDismissedFiles.add(agent.jsonlFile);
              this.knownJsonlFiles.delete(agent.jsonlFile);
            }
            removeAgent(
              id,
              this.agents,
              this.fileWatchers,
              this.pollingTimers,
              this.waitingTimers,
              this.permissionTimers,
              this.jsonlPollTimers,
              this.persistAgents,
            );
              sendMessage(this.viewerMessageRelay, { type: 'agentClosed', id });
          }
        }
          sendMessage(this.viewerMessageRelay, this.buildSettingsLoadedMessage());
      } else if (message.type === 'webviewReady') {
          await this.ensureRuntimeStarted();
          this.sendLocalBootstrap(webviewView.webview);
      } else if (message.type === 'requestDiagnostics') {
        // Send connection diagnostics for all agents to the Debug View
        const diagnostics: Array<Record<string, unknown>> = [];
        for (const [, agent] of this.agents) {
          let jsonlExists = false;
          let fileSize = 0;
          try {
            const stat = fs.statSync(agent.jsonlFile);
            jsonlExists = true;
            fileSize = stat.size;
          } catch {
            /* file doesn't exist */
          }
          diagnostics.push({
            id: agent.id,
            projectDir: agent.projectDir,
            projectDirExists: fs.existsSync(agent.projectDir),
            jsonlFile: agent.jsonlFile,
            jsonlExists,
            fileSize,
            fileOffset: agent.fileOffset,
            lastDataAt: agent.lastDataAt,
            linesProcessed: agent.linesProcessed,
          });
        }
        this.webview?.postMessage({ type: 'agentDiagnostics', agents: diagnostics });
      } else if (message.type === 'openSessionsFolder') {
        const projectDir = getProjectDirPath();
        if (projectDir && fs.existsSync(projectDir)) {
          vscode.env.openExternal(vscode.Uri.file(projectDir));
        }
      } else if (message.type === 'exportLayout') {
        const layout = readLayoutFromFile();
        if (!layout) {
          vscode.window.showWarningMessage('Pixel Agents: No saved layout to export.');
          return;
        }
        const uri = await vscode.window.showSaveDialog({
          filters: { 'JSON Files': ['json'] },
          defaultUri: vscode.Uri.file(path.join(os.homedir(), 'pixel-agents-layout.json')),
        });
        if (uri) {
          fs.writeFileSync(uri.fsPath, JSON.stringify(layout, null, 2), 'utf-8');
          vscode.window.showInformationMessage('Pixel Agents: Layout exported successfully.');
        }
      } else if (message.type === 'addExternalAssetDirectory') {
        const uris = await vscode.window.showOpenDialog({
          canSelectFolders: true,
          canSelectFiles: false,
          canSelectMany: false,
          openLabel: 'Select Asset Directory',
        });
        if (!uris || uris.length === 0) return;
        const newPath = uris[0].fsPath;
        const cfg = readConfig();
        if (!cfg.externalAssetDirectories.includes(newPath)) {
          cfg.externalAssetDirectories.push(newPath);
          writeConfig(cfg);
        }
        await this.reloadAndSendCharacters();
        await this.reloadAndSendFurniture();
        sendMessage(this.viewerMessageRelay, {
          type: 'externalAssetDirectoriesUpdated',
          dirs: cfg.externalAssetDirectories,
        });
      } else if (message.type === 'removeExternalAssetDirectory') {
        const cfg = readConfig();
        cfg.externalAssetDirectories = cfg.externalAssetDirectories.filter(
          (d) => d !== (message.path as string),
        );
        writeConfig(cfg);
        await this.reloadAndSendCharacters();
        await this.reloadAndSendFurniture();
        sendMessage(this.viewerMessageRelay, {
          type: 'externalAssetDirectoriesUpdated',
          dirs: cfg.externalAssetDirectories,
        });
      } else if (message.type === 'importLayout') {
        const uris = await vscode.window.showOpenDialog({
          filters: { 'JSON Files': ['json'] },
          canSelectMany: false,
        });
        if (!uris || uris.length === 0) return;
        try {
          const raw = fs.readFileSync(uris[0].fsPath, 'utf-8');
          const imported = JSON.parse(raw) as Record<string, unknown>;
          if (imported.version !== 1 || !Array.isArray(imported.tiles)) {
            vscode.window.showErrorMessage('Pixel Agents: Invalid layout file.');
            return;
          }
          this.layoutWatcher?.markOwnWrite();
          writeLayoutToFile(imported);
          sendMessage(this.viewerMessageRelay, { type: 'layoutLoaded', layout: imported });
          vscode.window.showInformationMessage('Pixel Agents: Layout imported successfully.');
        } catch {
          vscode.window.showErrorMessage('Pixel Agents: Failed to read or parse layout file.');
        }
      }
    });

    vscode.window.onDidChangeActiveTerminal((terminal) => {
      this.activeAgentId.current = null;
      if (!terminal) return;
      for (const [id, agent] of this.agents) {
        if (agent.terminalRef && agent.terminalRef === terminal) {
          this.activeAgentId.current = id;
          sendMessage(this.viewerMessageRelay, { type: 'agentSelected', id });
          break;
        }
      }
    });

    vscode.window.onDidCloseTerminal((closed) => {
      for (const [id, agent] of this.agents) {
        if (agent.terminalRef && agent.terminalRef === closed) {
          if (this.activeAgentId.current === id) {
            this.activeAgentId.current = null;
          }
          // If this is a team lead, remove its teammates
          if (agent.isTeamLead) {
            this.removeTeammates(id);
          }
          // Dismiss JSONL so external scanner doesn't re-adopt it
          dismissedJsonlFiles.set(agent.jsonlFile, Date.now());
          this.unregisterAgentHook(agent);
          removeAgent(
            id,
            this.agents,
            this.fileWatchers,
            this.pollingTimers,
            this.waitingTimers,
            this.permissionTimers,
            this.jsonlPollTimers,
            this.persistAgents,
          );
          sendMessage(this.viewerMessageRelay, { type: 'agentClosed', id });
        }
      }
    });
  }

  /** Export current saved layout as a versioned default-layout-{N}.json (dev utility) */
  exportDefaultLayout(): void {
    const layout = readLayoutFromFile();
    if (!layout) {
      vscode.window.showWarningMessage('Pixel Agents: No saved layout found.');
      return;
    }
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    if (!workspaceRoot) {
      vscode.window.showErrorMessage('Pixel Agents: No workspace folder found.');
      return;
    }
    const assetsDir = path.join(workspaceRoot, 'webview-ui', 'public', 'assets');

    // Find the next revision number
    let maxRevision = 0;
    if (fs.existsSync(assetsDir)) {
      for (const file of fs.readdirSync(assetsDir)) {
        const match = /^default-layout-(\d+)\.json$/.exec(file);
        if (match) {
          maxRevision = Math.max(maxRevision, parseInt(match[1], 10));
        }
      }
    }
    const nextRevision = maxRevision + 1;
    layout[LAYOUT_REVISION_KEY] = nextRevision;

    const targetPath = path.join(assetsDir, `default-layout-${nextRevision}.json`);
    const json = JSON.stringify(layout, null, 2);
    fs.writeFileSync(targetPath, json, 'utf-8');
    vscode.window.showInformationMessage(
      `Pixel Agents: Default layout exported as revision ${nextRevision} to ${targetPath}`,
    );
  }

  private async loadAllFurnitureAssets(): Promise<LoadedAssets | null> {
    if (!this.assetsRoot) return null;
    let assets = await loadFurnitureAssets(this.assetsRoot);
    const config = readConfig();
    for (const extraDir of config.externalAssetDirectories) {
      console.log('[Extension] Loading external assets from:', extraDir);
      const extra = await loadFurnitureAssets(extraDir);
      if (extra) {
        assets = assets ? mergeLoadedAssets(assets, extra) : extra;
      }
    }
    return assets;
  }

  private async loadAllCharacterSprites(): Promise<LoadedCharacterSprites | null> {
    if (!this.assetsRoot) return null;
    let chars = await loadCharacterSprites(this.assetsRoot);
    const config = readConfig();
    for (const extraDir of config.externalAssetDirectories) {
      console.log('[Extension] Loading external character sprites from:', extraDir);
      const extra = await loadExternalCharacterSprites(extraDir);
      if (extra) {
        chars = chars ? mergeCharacterSprites(chars, extra) : extra;
      }
    }
    return chars;
  }

  private async loadRuntimeAssets(): Promise<void> {
    console.log('[Extension] Loading viewer runtime assets...');
    const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
    const extensionPath = this.extensionUri.fsPath;
    const bundledAssetsDir = path.join(extensionPath, 'dist', 'assets');

    let assetsRoot: string | null = null;
    if (fs.existsSync(bundledAssetsDir)) {
      assetsRoot = path.join(extensionPath, 'dist');
    } else if (workspaceRoot) {
      assetsRoot = workspaceRoot;
    }

    this.assetsRoot = assetsRoot;
    if (!assetsRoot) {
      console.log('[Extension] ⚠️  No assets directory found for runtime bootstrap');
      return;
    }

    this.defaultLayout = loadDefaultLayout(assetsRoot);
    this.loadedCharacterSprites = await this.loadAllCharacterSprites();
    this.loadedFloorTiles = await loadFloorTiles(assetsRoot);
    this.loadedWallTiles = await loadWallTiles(assetsRoot);
    this.loadedAssets = await this.loadAllFurnitureAssets();
  }

  private getRemoteViewerUrl(): string | undefined {
    const config = this.pixelAgentsServer?.getConfig();
    if (!config) return undefined;

    const interfaces = os.networkInterfaces();
    for (const entries of Object.values(interfaces)) {
      if (!entries) continue;
      for (const entry of entries) {
        if (entry.family === 'IPv4' && !entry.internal) {
          return `http://${entry.address}:${config.port.toString()}/viewer/?token=${encodeURIComponent(config.token)}`;
        }
      }
    }

    return `http://127.0.0.1:${config.port.toString()}/viewer/?token=${encodeURIComponent(config.token)}`;
  }

  async startSourceRuntime(): Promise<void> {
    await this.ensureRuntimeStarted();
  }

  async copyRemoteViewerUrl(): Promise<void> {
    await this.ensureRuntimeStarted();
    await this.serverReadyPromise;
    const url = this.getRemoteViewerUrl();
    if (!url) {
      vscode.window.showErrorMessage('Pixel Agents: Remote viewer URL is not available.');
      return;
    }
    await vscode.env.clipboard.writeText(url);
    vscode.window.showInformationMessage('Pixel Agents: Remote viewer URL copied to clipboard.');
  }

  private async reloadAndSendFurniture(): Promise<void> {
    if (!this.assetsRoot) return;
    try {
      this.loadedAssets = await this.loadAllFurnitureAssets();
      if (this.loadedAssets) {
        sendAssetsToWebview(this.viewerMessageRelay, this.loadedAssets);
      }
    } catch (err) {
      console.error('[Extension] Error reloading furniture assets:', err);
    }
  }

  private async reloadAndSendCharacters(): Promise<void> {
    if (!this.assetsRoot) return;
    try {
      this.loadedCharacterSprites = await this.loadAllCharacterSprites();
      if (this.loadedCharacterSprites) {
        sendCharacterSpritesToWebview(this.viewerMessageRelay, this.loadedCharacterSprites);
      }
    } catch (err) {
      console.error('[Extension] Error reloading character sprites:', err);
    }
  }

  private startLayoutWatcher(): void {
    if (this.layoutWatcher) return;
    this.layoutWatcher = watchLayoutFile((layout) => {
      console.log('[Pixel Agents] External layout change — pushing to webview');
      sendMessage(this.viewerMessageRelay, { type: 'layoutLoaded', layout });
    });
  }

  dispose() {
    this.pixelAgentsServer?.stop();
    this.pixelAgentsServer = null;
    this.hookEventHandler?.dispose();
    this.hookEventHandler = null;
    this.layoutWatcher?.dispose();
    this.layoutWatcher = null;
    for (const id of [...this.agents.keys()]) {
      removeAgent(
        id,
        this.agents,
        this.fileWatchers,
        this.pollingTimers,
        this.waitingTimers,
        this.permissionTimers,
        this.jsonlPollTimers,
        this.persistAgents,
      );
    }
    if (this.projectScanTimer.current) {
      clearInterval(this.projectScanTimer.current);
      this.projectScanTimer.current = null;
    }
    if (this.externalScanTimer) {
      clearInterval(this.externalScanTimer);
      this.externalScanTimer = null;
    }
    if (this.staleCheckTimer) {
      clearInterval(this.staleCheckTimer);
      this.staleCheckTimer = null;
    }
  }
}

function getWebviewContent(webview: vscode.Webview, extensionUri: vscode.Uri): string {
  const distPath = vscode.Uri.joinPath(extensionUri, 'dist', 'webview');
  const indexPath = vscode.Uri.joinPath(distPath, 'index.html').fsPath;

  let html = fs.readFileSync(indexPath, 'utf-8');

  html = html.replace(/(href|src)="\.\/([^"]+)"/g, (_match, attr, filePath) => {
    const fileUri = vscode.Uri.joinPath(distPath, filePath);
    const webviewUri = webview.asWebviewUri(fileUri);
    return `${attr}="${webviewUri}"`;
  });

  return html;
}
