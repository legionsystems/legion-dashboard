import { Link, Navigate, Route, Routes } from "react-router-dom";
import WorkItemList from "./pages/WorkItemList.jsx";
import WorkItemDetail from "./pages/WorkItemDetail.jsx";
import WorkItemForm from "./pages/WorkItemForm.jsx";

export default function App() {
  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <h1 className="text-xl font-semibold tracking-tight">
            LEGION Dashboard
          </h1>
          <nav className="flex gap-4 text-sm">
            <Link
              to="/work-items"
              className="text-slate-600 hover:text-slate-900"
            >
              Work Items
            </Link>
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <Routes>
          <Route path="/" element={<Navigate to="/work-items" replace />} />
          <Route path="/work-items" element={<WorkItemList />} />
          <Route path="/work-items/new" element={<WorkItemForm />} />
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
          <Route path="/work-items/:id/edit" element={<WorkItemForm />} />
        </Routes>
      </main>
    </div>
  );
}
