import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    title: 'Stratum · Studio',
    loadComponent: () => import('./features/studio/studio').then((m) => m.StudioComponent),
  },
  {
    path: 'learn',
    title: 'Stratum · How ADK graphs work',
    loadComponent: () => import('./features/learn/learn').then((m) => m.LearnComponent),
  },
  { path: '**', redirectTo: '' },
];
