type ThemeLogoProps = {
  height: string;
  width: string;
};

export default function ThemeLogo({ height, width }: ThemeLogoProps) {
  return (
    <span className="brand-logo" style={{ height, width }}>
      <img
        className="brand-logo-light"
        src="/images/logo2.png"
        alt="Telegram Depiler Logo"
      />
      <img
        className="brand-logo-dark"
        src="/images/logo2-dark.png"
        alt=""
        aria-hidden="true"
      />
    </span>
  );
}
