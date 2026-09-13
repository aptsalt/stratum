import { Injectable, signal } from '@angular/core';

export type DockTab = 'run' | 'code' | 'ir' | 'problems';
export type StudioView = 'graph' | 'chat' | 'script';

/** Chrome state shared by the shell, studio and command palette. */
@Injectable({ providedIn: 'root' })
export class UiStore {
  readonly paletteOpen = signal(false);
  readonly dockOpen = signal(true);
  readonly dockTab = signal<DockTab>('run');
  readonly theme = signal<'dark' | 'light'>(readTheme());
  readonly view = signal<StudioView>(read('stratum.view', ['graph', 'chat', 'script'], 'graph'));
  /** Script text handed from a chat proposal to the Script view. */
  readonly scriptHandoff = signal<string | null>(null);

  openDock(tab: DockTab): void {
    this.dockTab.set(tab);
    this.dockOpen.set(true);
  }

  setView(view: StudioView): void {
    this.view.set(view);
    write('stratum.view', view);
  }

  toggleTheme(): void {
    const next = this.theme() === 'dark' ? 'light' : 'dark';
    this.theme.set(next);
    document.documentElement.setAttribute('data-theme', next);
    write('stratum.theme', next);
  }
}

function read<T extends string>(key: string, allowed: T[], fallback: T): T {
  try {
    const v = localStorage.getItem(key) as T | null;
    return v && allowed.includes(v) ? v : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode — preferences are a convenience only */
  }
}

function readTheme(): 'dark' | 'light' {
  const t = read('stratum.theme', ['dark', 'light'], 'dark');
  document.documentElement.setAttribute('data-theme', t);
  return t;
}
