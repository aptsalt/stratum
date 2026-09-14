import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { ApiService } from './core/api';
import { DEMO } from './core/demo';
import { DemoEngine } from './core/demo-engine';
import { GraphStore } from './core/graph-store';
import { Health } from './core/models';
import { UiStore } from './core/ui-store';

@Component({
  selector: 'app-root',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class App {
  readonly ui = inject(UiStore);
  private readonly graph = inject(GraphStore);
  private readonly api = inject(ApiService);

  readonly demo = DEMO;
  readonly engine = DEMO ? inject(DemoEngine) : null;
  readonly health = signal<Health | null>(null);
  readonly checked = signal(false);
  readonly status = computed(() => {
    const h = this.health();
    if (this.engine) {
      if (this.engine.failed()) return { tone: 'bad', text: 'Demo failed to load' };
      return this.engine.ready() ? { tone: 'ok', text: 'In-browser demo · simulated runs' } : { tone: '', text: 'Loading compiler…' };
    }
    if (!this.checked()) return { tone: '', text: 'Connecting…' };
    if (!h) return { tone: 'bad', text: 'Backend offline' };
    return { tone: 'ok', text: `ADK ${h.adk} · ${h.engines.gemini ? 'Gemini ready' : 'mock engine'}` };
  });

  constructor() {
    void this.ping();
    const timer = setInterval(() => void this.ping(), 5000);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  private async ping(): Promise<void> {
    const wasOffline = !this.health();
    try {
      this.health.set(await this.api.health());
      if (wasOffline || !this.graph.templates().length) await this.graph.init();
    } catch {
      this.health.set(null);
    } finally {
      this.checked.set(true);
    }
  }
}
