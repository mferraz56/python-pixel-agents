import * as vscode from 'vscode';

import {
  COMMAND_COPY_REMOTE_VIEWER_URL,
  COMMAND_EXPORT_DEFAULT_LAYOUT,
  COMMAND_SHOW_PANEL,
  VIEW_ID,
} from './constants.js';
import { PixelAgentsViewProvider } from './PixelAgentsViewProvider.js';

let providerInstance: PixelAgentsViewProvider | undefined;

export function activate(context: vscode.ExtensionContext) {
  console.log(`[Pixel Agents] PIXEL_AGENTS_DEBUG=${process.env.PIXEL_AGENTS_DEBUG ?? 'not set'}`);
  const provider = new PixelAgentsViewProvider(context);
  providerInstance = provider;
  void provider.startSourceRuntime();

  context.subscriptions.push(vscode.window.registerWebviewViewProvider(VIEW_ID, provider));

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMAND_SHOW_PANEL, () => {
      vscode.commands.executeCommand(`${VIEW_ID}.focus`);
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMAND_EXPORT_DEFAULT_LAYOUT, () => {
      provider.exportDefaultLayout();
    }),
  );

  context.subscriptions.push(
    vscode.commands.registerCommand(COMMAND_COPY_REMOTE_VIEWER_URL, async () => {
      await provider.copyRemoteViewerUrl();
    }),
  );
}

export function deactivate() {
  providerInstance?.dispose();
}
