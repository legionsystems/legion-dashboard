import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import Dashboard from "./pages/Dashboard.jsx";
import WorkItemList from "./pages/WorkItemList.jsx";
import WorkItemDetail from "./pages/WorkItemDetail.jsx";
import WorkItemForm from "./pages/WorkItemForm.jsx";
import WorkItemNew from "./pages/WorkItemNew.jsx";
import WorkIntakeForm from "./components/WorkIntakeForm.jsx";
import Apps from "./pages/Apps.jsx";
import Settings from "./pages/Settings.jsx";
import ModelHosts from "./pages/ModelHosts.jsx";
import ExecutorAllowlist from "./pages/ExecutorAllowlist.jsx";
import { Sidebar, MobileNav, TopBar } from "./components/shell.jsx";

function crumbsFromPath(pathname) {
  if (pathname === "/" || pathname === "") return ["DASHBOARD"];
  if (pathname.match(/^\/work-items\/new\/[a-z-]+$/)) {
    const type = pathname.split("/").pop();
    return ["WORK ITEMS", "NEW", type.toUpperCase()];
  }
  if (pathname === "/work-items/new") return ["WORK ITEMS", "NEW"];
  if (pathname.match(/^\/work-items\/\d+\/edit$/))
    return ["WORK ITEMS", "EDIT"];
  if (pathname.match(/^\/work-items\/\d+$/)) {
    const id = pathname.split("/").pop();
    return ["WORK ITEMS", `#${id}`];
  }
  if (pathname.startsWith("/work-items")) return ["WORK ITEMS"];
  if (pathname.startsWith("/apps")) return ["APPS"];
  if (pathname === "/settings/model-providers") return ["SETTINGS", "MODEL PROVIDERS"];
  if (pathname === "/settings/executor-allowlist") return ["SETTINGS", "EXECUTOR ALLOWLIST"];
  if (pathname === "/settings") return ["SETTINGS"];
  return [pathname.toUpperCase()];
}

export default function App() {
  const { pathname } = useLocation();
  const crumbs = crumbsFromPath(pathname);
  return (
    <div className="flex min-h-screen bg-canvas text-fg-primary">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        <TopBar crumbs={crumbs} />
        <MobileNav />
        <main className="flex-1 overflow-x-hidden">
          <div className="mx-auto max-w-7xl px-4 md:px-6 py-5 md:py-6">
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/work-items" element={<WorkItemList />} />
              <Route path="/work-items/new" element={<WorkItemNew />} />
              <Route
                path="/work-items/new/:type"
                element={<WorkIntakeForm />}
              />
              <Route path="/work-items/:id" element={<WorkItemDetail />} />
              <Route path="/work-items/:id/edit" element={<WorkItemForm />} />
              <Route path="/apps" element={<Apps />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/settings/model-providers" element={<ModelHosts />} />
              <Route path="/settings/executor-allowlist" element={<ExecutorAllowlist />} />
              {/* Legacy route - redirects to new location */}
              <Route path="/model-hosts" element={<Navigate to="/settings/model-providers" replace />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </main>
      </div>
    </div>
  );
}
