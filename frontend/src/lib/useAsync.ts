import { useEffect, useState } from 'react';

export type Async<T> =
  | { status: 'loading' }
  | { status: 'ready'; data: T }
  | { status: 'error'; error: Error };

/**
 * Minimal async state. Errors surface — there is no silent fallback and no
 * placeholder data, because a screen quietly showing zeros is worse than a
 * screen saying it could not load.
 */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[] = []): Async<T> {
  const [state, setState] = useState<Async<T>>({ status: 'loading' });

  useEffect(() => {
    let alive = true;
    setState({ status: 'loading' });

    load()
      .then((data) => {
        if (alive) setState({ status: 'ready', data });
      })
      .catch((error: unknown) => {
        if (alive) {
          setState({
            status: 'error',
            error: error instanceof Error ? error : new Error(String(error)),
          });
        }
      });

    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
