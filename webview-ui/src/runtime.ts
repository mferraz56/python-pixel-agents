/**
 * Runtime detection, provider-agnostic
 *
 * Single source of truth for determining whether the webview is running
 * inside an IDE extension (VS Code, Cursor, Windsurf, etc.) or standalone
 * in a browser.
 */

declare function acquireVsCodeApi(): unknown;

type Runtime = 'vscode' | 'browser' | 'browser-remote';

const hasVsCodeApi = typeof acquireVsCodeApi !== 'undefined';
const searchParams = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
const hasRemoteToken = !!searchParams?.get('token');

const runtime: Runtime = hasVsCodeApi ? 'vscode' : hasRemoteToken ? 'browser-remote' : 'browser';

export const isVsCodeRuntime = runtime === 'vscode';
export const isBrowserRuntime = runtime !== 'vscode';
export const isRemoteViewerRuntime = runtime === 'browser-remote';
