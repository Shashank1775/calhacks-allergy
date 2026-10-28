/** A blob URL for a picked file, revoked when the file changes or the component goes. */
import { useEffect, useState } from "react";

export function useObjectUrl(file: File | null): string | null {
  const [url, set_url] = useState<string | null>(null);

  useEffect(() => {
    // jsdom has no createObjectURL; the picture is a nicety in tests, not required.
    if (!file || typeof URL.createObjectURL !== "function") {
      set_url(null);
      return;
    }
    const next = URL.createObjectURL(file);
    set_url(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);

  return url;
}
