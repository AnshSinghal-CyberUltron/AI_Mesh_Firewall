import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/module2/PageHeader";
import { DataTable } from "../../components/module2/DataTable";

export function SettingsTeamPage() {
  const { fetchWithAuth, user } = useAuth();
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  const isAdmin = user?.is_superuser || (user?.roles || []).includes("platform_admin");

  useEffect(() => {
    if (!isAdmin) { setLoading(false); return; }
    (async () => {
      setLoading(true);
      try {
        const res = await fetchWithAuth("/api/auth/users/?offering=platform");
        const data = await res.json();
        setUsers(data.results || data);
      } finally {
        setLoading(false);
      }
    })();
  }, [isAdmin]);

  if (!isAdmin) {
    return (
      <div>
        <PageHeader title="Team & RBAC" subtitle="Admin access required" />
        <p className="text-sm text-slate-500">Contact your platform admin to manage team members.</p>
      </div>
    );
  }

  if (loading) return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-teal-500" /></div>;

  return (
    <div>
      <PageHeader title="Team & RBAC" subtitle="Manage team members and role assignments" />
      <DataTable
        columns={[
          { key: "email", label: "Email" },
          { key: "first_name", label: "Name", render: (r) => [r.first_name, r.last_name].filter(Boolean).join(" ") || "—" },
          { key: "roles", label: "Roles", render: (r) => (r.roles || []).join(", ") },
          { key: "is_active", label: "Active", render: (r) => r.is_active ? "Yes" : "No" },
        ]}
        rows={users}
      />
    </div>
  );
}
