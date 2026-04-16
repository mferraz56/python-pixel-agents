export type AgentStatusValue = 'active' | 'waiting';

export type AgentCreatedMessage = {
  type: 'agentCreated';
  id: number;
  folderName?: string;
  isExternal?: boolean;
  isTeammate?: boolean;
  teammateName?: string;
  parentAgentId?: number;
  teamName?: string;
};

export type AgentClosedMessage = {
  type: 'agentClosed';
  id: number;
};

export type AgentSelectedMessage = {
  type: 'agentSelected';
  id: number;
};

export type AgentToolStartMessage = {
  type: 'agentToolStart';
  id: number;
  toolId: string;
  status: string;
  toolName?: string;
  permissionActive?: boolean;
  runInBackground?: boolean;
};

export type AgentToolDoneMessage = {
  type: 'agentToolDone';
  id: number;
  toolId: string;
};

export type AgentToolsClearMessage = {
  type: 'agentToolsClear';
  id: number;
};

export type AgentStatusMessage = {
  type: 'agentStatus';
  id: number;
  status: AgentStatusValue;
};

export type AgentToolPermissionMessage = {
  type: 'agentToolPermission';
  id: number;
};

export type AgentToolPermissionClearMessage = {
  type: 'agentToolPermissionClear';
  id: number;
};

export type SubagentToolStartMessage = {
  type: 'subagentToolStart';
  id: number;
  parentToolId: string;
  toolId: string;
  status: string;
};

export type SubagentToolDoneMessage = {
  type: 'subagentToolDone';
  id: number;
  parentToolId: string;
  toolId: string;
};

export type SubagentClearMessage = {
  type: 'subagentClear';
  id: number;
  parentToolId: string;
};

export type SubagentToolPermissionMessage = {
  type: 'subagentToolPermission';
  id: number;
  parentToolId: string;
};

export type AgentTeamInfoMessage = {
  type: 'agentTeamInfo';
  id: number;
  teamName?: string;
  agentName?: string;
  isTeamLead?: boolean;
  leadAgentId?: number;
  teamUsesTmux?: boolean;
};

export type AgentTokenUsageMessage = {
  type: 'agentTokenUsage';
  id: number;
  inputTokens: number;
  outputTokens: number;
};

export type ExistingAgentsMessage = {
  type: 'existingAgents';
  agents: number[];
  agentMeta?: Record<string | number, { palette?: number; hueShift?: number; seatId?: string }>;
  folderNames?: Record<number, string>;
  externalAgents?: Record<number, boolean>;
};

export type LayoutLoadedMessage = {
  type: 'layoutLoaded';
  layout: unknown;
  wasReset?: boolean;
};

export type FurnitureAssetsLoadedMessage = {
  type: 'furnitureAssetsLoaded';
  catalog: unknown[];
  sprites: Record<string, string[][]>;
};

export type FloorTilesLoadedMessage = {
  type: 'floorTilesLoaded';
  sprites: string[][][];
};

export type WallTilesLoadedMessage = {
  type: 'wallTilesLoaded';
  sets: string[][][][];
};

export type CharacterSpritesLoadedMessage = {
  type: 'characterSpritesLoaded';
  characters: unknown[];
};

export type SettingsLoadedMessage = {
  type: 'settingsLoaded';
  soundEnabled?: boolean;
  lastSeenVersion?: string;
  extensionVersion?: string;
  watchAllSessions?: boolean;
  alwaysShowLabels?: boolean;
  hooksEnabled?: boolean;
  hooksInfoShown?: boolean;
  externalAssetDirectories?: string[];
  remoteViewerUrl?: string;
};

export type WorkspaceFoldersMessage = {
  type: 'workspaceFolders';
  folders: Array<{ name: string; path: string }>;
};

export type ExternalAssetDirectoriesUpdatedMessage = {
  type: 'externalAssetDirectoriesUpdated';
  dirs: string[];
};

export type AgentDiagnosticsMessage = {
  type: 'agentDiagnostics';
  agents: Array<Record<string, unknown>>;
};

export type GenericViewerMessage = {
  type: string;
  [key: string]: unknown;
};

export type ViewerMessage =
  | AgentCreatedMessage
  | AgentClosedMessage
  | AgentSelectedMessage
  | AgentToolStartMessage
  | AgentToolDoneMessage
  | AgentToolsClearMessage
  | AgentStatusMessage
  | AgentToolPermissionMessage
  | AgentToolPermissionClearMessage
  | SubagentToolStartMessage
  | SubagentToolDoneMessage
  | SubagentClearMessage
  | SubagentToolPermissionMessage
  | AgentTeamInfoMessage
  | AgentTokenUsageMessage
  | ExistingAgentsMessage
  | LayoutLoadedMessage
  | FurnitureAssetsLoadedMessage
  | FloorTilesLoadedMessage
  | WallTilesLoadedMessage
  | CharacterSpritesLoadedMessage
  | SettingsLoadedMessage
  | WorkspaceFoldersMessage
  | ExternalAssetDirectoriesUpdatedMessage
  | AgentDiagnosticsMessage
  | GenericViewerMessage;

export interface MessageSender {
  postMessage(message: ViewerMessage): void | Promise<boolean>;
}

export function sendMessage(
  messageSender: MessageSender | undefined,
  message: ViewerMessage,
): void {
  void messageSender?.postMessage(message);
}