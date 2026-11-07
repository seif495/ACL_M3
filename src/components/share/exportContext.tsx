import { createContext, useContext, type ReactNode } from 'react';

// Kept apart from the assistant cards (which bring framer-motion) so pages in the main bundle
// can share as an image without pulling the cards in.

export type ShareState = 'idle' | 'working' | 'ready' | 'failed';

export interface ExportContextValue {
  /** This copy of the card is being drawn into an image: photos load through our proxy,
      links and buttons give way to the Pitch Me mark. */
  exporting: boolean;
  share?: () => void;
  shareState?: ShareState;
  /** 'share' opens the device's share sheet (phones); 'save' downloads a PNG. */
  shareMode?: 'share' | 'save';
}

const ExportContext = createContext<ExportContextValue>({ exporting: false });

export function ExportProvider({ value, children }: { value: ExportContextValue; children: ReactNode }) {
  return <ExportContext.Provider value={value}>{children}</ExportContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs with its provider
export function useExport(): ExportContextValue {
  return useContext(ExportContext);
}
