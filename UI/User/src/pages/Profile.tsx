import React, { useState, useEffect } from 'react';
import { getProfile, updateProfile } from '../services/auth';
import { User, Loader2, CheckCircle2 } from 'lucide-react';

const Profile: React.FC = () => {
  const [profile, setProfile] = useState<{ username: string, name: string, email: string } | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const data = getProfile();
    if (data) {
      setProfile(data);
      setName(data.name || '');
      setEmail(data.email || '');
    }
    setLoading(false);
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaved(false);
    await updateProfile({ name, email });
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 3000);
  };

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100%' }}>
        <Loader2 className="animate-spin text-blue-500" size={32} />
      </div>
    );
  }

  if (!profile) {
    return <div>Profile not found.</div>;
  }

  return (
    <div className="fade-in" style={{ maxWidth: '600px', margin: '0 auto' }}>
      <h1>My Profile</h1>
      
      <div className="card">
        <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginBottom: '2rem' }}>
          <div style={{ width: '64px', height: '64px', borderRadius: '50%', backgroundColor: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <User size={32} color="var(--text-secondary)" />
          </div>
          <div>
            <h2 style={{ margin: 0 }}>{profile.username}</h2>
            <p style={{ margin: 0, fontSize: '0.875rem' }}>User Account</p>
          </div>
        </div>

        <form onSubmit={handleSave}>
          <div className="form-group">
            <label className="form-label">Full Name</label>
            <input 
              type="text" 
              className="form-input" 
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="form-group">
            <label className="form-label">Email Address</label>
            <input 
              type="email" 
              className="form-input" 
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '1rem', marginTop: '2rem' }}>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
            {saved && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--status-success)', fontSize: '0.875rem' }}>
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
