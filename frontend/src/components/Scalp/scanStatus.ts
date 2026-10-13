/** What a Scalp-page scanner tells the tab bar: when it last scanned, and whether it is scanning now. */
export type ScanStatus = { when: string | null; running: boolean };

export type OnScanStatus = (status: ScanStatus) => void;
