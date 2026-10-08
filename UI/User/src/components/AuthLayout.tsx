import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import Brand from "./Brand";
import ComputeArtwork from "./ComputeArtwork";

export default function AuthLayout({
  children,
  register = false,
}: {
  children: ReactNode;
  register?: boolean;
}) {
  return (
    <div className="auth-page">
      <header className="auth-header">
        <Link to="/login" aria-label="DistributeML home">
          <Brand />
        </Link>
        <span className="eyebrow">A home for your experiments</span>
      </header>
      <div className="auth-body">
        <aside className="auth-showcase">
          <span className="eyebrow">Systems / Models / Possibilities</span>
          <h1>
            Big ideas.
            <br />
            <span>Room to run.</span>
          </h1>
          <p className="auth-tagline">
            A considered space for distributed training.
            <br />
            Bring your code. We'll make room for the compute.
          </p>
          <ComputeArtwork />
          <div className="auth-capabilities">
            <span>
              01 <strong>Build once</strong>
            </span>
            <span>
              02 <strong>Train at scale</strong>
            </span>
            <span>
              03 <strong>Explore live</strong>
            </span>
          </div>
        </aside>
        <main className="auth-form-side">
          <div className="auth-card fade-in">
            <span className="eyebrow">
              {register ? "Start something good" : "Pick up where you left off"}
            </span>
            <h2>{register ? "Your next chapter." : "Welcome back."}</h2>
            <p className="auth-description">
              {register
                ? "Create your account and give your ideas a workspace."
                : "Sign in to your personal compute workspace."}
            </p>
            {children}
          </div>
          <div className="auth-switch">
            {register ? "Already have a workspace?" : "New around here?"}{" "}
            <Link to={register ? "/login" : "/register"}>
              {register ? "Sign in" : "Create an account"}
              <ArrowUpRight size={14} />
            </Link>
          </div>
        </main>
      </div>
      <footer className="auth-footer">
        <span>Distributed compute. Considered.</span>
        <span>DistributeML / The compute studio</span>
      </footer>
    </div>
  );
}
