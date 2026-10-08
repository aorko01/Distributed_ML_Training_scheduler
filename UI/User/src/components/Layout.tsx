import { useEffect, useState } from "react";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  ArrowUpRight,
  ChevronRight,
  LogOut,
  Menu,
  Plus,
  User,
  X,
} from "lucide-react";
import { logout, getUsername } from "../services/auth";
import { RuntimeWatcher } from "../features/interactive-capacity/RuntimeWatcher";
import Brand from "./Brand";

const navigation = [
  { to: "/", label: "Overview", number: "01", end: true },
  { to: "/builds", label: "Image builds", number: "02" },
  { to: "/training", label: "Training", number: "03" },
  { to: "/interactive", label: "Interactive workspaces", number: "04" },
  { to: "/job-history", label: "Job history", number: "05" },
];

export default function Layout() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [mobile, setMobile] = useState(
    () =>
      typeof window.matchMedia === "function" &&
      window.matchMedia("(max-width: 900px)").matches,
  );
  let username = "Researcher";
  try {
    username = getUsername() || username;
  } catch {
    /* Storage can be unavailable in embedded environments. */
  }
  const currentPage =
    pathname === "/submit"
      ? "New workspace"
      : pathname === "/profile"
        ? "Your profile"
        : pathname.startsWith("/jobs/")
          ? "Training / Run details"
          : navigation.find(
              (item) => item.to !== "/" && pathname.startsWith(item.to),
            )?.label || "Overview";

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const query = window.matchMedia("(max-width: 900px)");
    const update = () => {
      setMobile(query.matches);
      setMenuOpen(false);
    };
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  function handleLogout() {
    const user = getUsername();
    if (user) {
      try {
        sessionStorage.removeItem(`interactive-seen-${user}`);
      } catch {
        /* ignore */
      }
    }
    logout();
    navigate("/login");
  }

  return (
    <div className="app-container">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          onClick={() => setMenuOpen(false)}
          aria-label="Close navigation"
        />
      )}
      <aside
        id="studio-navigation"
        className={`sidebar${menuOpen ? " sidebar--open" : ""}`}
        inert={mobile && !menuOpen}
      >
        <Link className="sidebar-brand" to="/" aria-label="DistributeML home">
          <Brand />
        </Link>
        <div className="sidebar-intro">
          <span className="eyebrow">The compute studio</span>
          <p>
            A little closer
            <br />
            to the metal.
          </p>
        </div>
        <Link to="/submit" className="new-workspace">
          <Plus size={16} /> New workspace{" "}
          <span className="new-workspace-arrow">↗</span>
        </Link>
        <nav className="sidebar-nav" aria-label="Main navigation">
          <span className="nav-caption">Your workspace</span>
          {navigation.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `nav-item${isActive ? " active" : ""}`
              }
            >
              <span className="nav-number">{item.number}</span>
              <span>{item.label}</span>
              <ArrowUpRight size={14} className="nav-arrow" />
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="eyebrow">Built for the process</span>
            <p>
              From the first experiment
              <br />
              to the final checkpoint.
            </p>
            <div className="sidebar-rule" />
          </div>
          <NavLink
            to="/profile"
            className={({ isActive }) =>
              `account-link${isActive ? " active" : ""}`
            }
          >
            <span className="avatar">{username.slice(0, 2).toUpperCase()}</span>
            <span className="account-text">
              <strong>{username}</strong>
              <span>Personal workspace</span>
            </span>
            <ChevronRight size={14} />
          </NavLink>
        </div>
      </aside>
      <main className="main-content" id="main-content">
        <header className="top-header">
          <button
            className="mobile-toggle"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={menuOpen}
            aria-controls="studio-navigation"
          >
            {menuOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
          <div className="breadcrumb">
            <span>studio</span>
            <span className="breadcrumb-slash">/</span>
            <span>{currentPage}</span>
          </div>
          <div className="header-actions">
            <NavLink to="/profile" className="header-link" aria-label="Profile">
              <User size={15} />
              <span>Profile</span>
            </NavLink>
            <button
              onClick={handleLogout}
              className="header-link"
              aria-label="Sign out"
            >
              <LogOut size={15} />
              <span>Sign out</span>
            </button>
          </div>
        </header>
        <div className="page-content">
          <Outlet />
          <RuntimeWatcher />
        </div>
        <footer className="studio-footer">
          <span>
            <span className="footer-mark">dml_</span> Distributed compute.
            Considered.
          </span>
          <span>Build · Train · Explore</span>
        </footer>
      </main>
    </div>
  );
}
