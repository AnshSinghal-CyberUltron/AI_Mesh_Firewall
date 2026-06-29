import { useId, useState } from "react";
import { Input } from "../../ui/Input";

export function PasswordInput({ value, onChange, placeholder, id }) {
  const [show, setShow] = useState(false);
  const reactId = useId();

  return (
    <div className="relative">
      <Input
        id={id ?? reactId}
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="pr-20 font-mono"
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        className="absolute right-2 top-1/2 -translate-y-1/2 rounded px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      >
        {show ? "Hide" : "Show"}
      </button>
    </div>
  );
}
