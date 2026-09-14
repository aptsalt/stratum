declare const STRATUM_DEMO: boolean | undefined;

/** True in the GitHub Pages build — angular.json's "demo" configuration defines STRATUM_DEMO. */
export const DEMO = typeof STRATUM_DEMO !== 'undefined' && STRATUM_DEMO === true;
