import PageHeading from "../components/PageHeading";
import React, { useState, useEffect } from "react";
import {
  getProfile,
  updateProfile,
  type User as UserProfile,
} from "../services/auth";
import { Loader2, CheckCircle2 } from "lucide-react";

const Profile: React.FC = () => {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    const loadProfile = async () => {
      try {
        const data = await getProfile();
        setProfile(data);
        setName(data.name || "");
        setEmail(data.email || "");
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to load profile.",
        );
      } finally {
        setLoading(false);
      }
    };
    loadProfile();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    setError("");
    try {
      await updateProfile({ name, email });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to update profile.",
      );
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div
        style={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "100%",
        }}
      >
        <Loader2 className="animate-spin text-accent" size={32} />
      </div>
    );
  }

  if (!profile) {
    return <div>Profile not found.</div>;
  }

  return (
    <div className="fade-in profile-page">
      <PageHeading
        eyebrow="Your corner of the studio"
        title="Make yourself at home."
        description="The person behind the experiments. Keep your account details up to date."
      />
      <div className="card">
        <div className="profile-identity">
          <span className="avatar">
            {profile.username.slice(0, 2).toUpperCase()}
          </span>
          <div>
            <h2>{profile.username}</h2>
            <p>Personal workspace / Account settings</p>
          </div>
        </div>

        <form onSubmit={handleSave}>
          {error && (
            <div
              style={{
                padding: "0.75rem",
                backgroundColor: "var(--danger-muted)",
                color: "var(--status-failed)",
                borderRadius: "6px",
                marginBottom: "1rem",
                fontSize: "0.875rem",
              }}
            >
              {error}
            </div>
          )}
          <div className="form-group">
            <label className="form-label" htmlFor="profile-name">
              Full name
            </label>
            <input
              id="profile-name"
              type="text"
              className="form-input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="profile-email">
              Email address
            </label>
            <input
              id="profile-email"
              type="email"
              className="form-input"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "1rem",
              marginTop: "2rem",
            }}
          >
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Saving..." : "Save Changes"}
            </button>
            {saved && (
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  color: "var(--status-success)",
                  fontSize: "0.875rem",
                }}
              >
                <CheckCircle2 size={16} />
                Profile updated
              </div>
            )}
          </div>
        </form>
      </div>
    </div>
  );
};

export default Profile;
