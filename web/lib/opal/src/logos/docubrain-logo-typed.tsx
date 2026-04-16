import SvgDocubrainLogo from "@opal/logos/docubrain-logo";
import SvgDocubrainTyped from "@opal/logos/docubrain-typed";
import { cn } from "@opal/utils";

interface DocubrainLogoTypedProps {
  size?: number;
  className?: string;
}

// # NOTE(@raunakab):
// This ratio is not some random, magical number; it is available on Figma.
const HEIGHT_TO_GAP_RATIO = 5 / 16;

const SvgDocubrainLogoTyped = ({ size: height, className }: DocubrainLogoTypedProps) => {
  const gap = height != null ? height * HEIGHT_TO_GAP_RATIO : undefined;

  return (
    <div
      className={cn(`flex flex-row items-center`, className)}
      style={{ gap }}
    >
      <SvgDocubrainLogo size={height} />
      <SvgDocubrainTyped size={height} />
    </div>
  );
};
export default SvgDocubrainLogoTyped;
