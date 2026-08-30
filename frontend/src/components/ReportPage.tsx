import React, { useEffect, useState } from 'react';

const AUTH_URL = `${process.env.REACT_APP_AUTH_URL}`;

const ReportPage: React.FC = () => {
  const [user, setUser] = useState<any>(null);
  const [initialized, setInitialized] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState('');

  const loadUser = async () => {
    try {
      const response = await fetch(
        `${AUTH_URL}/auth/me`,
        {
          credentials: 'include',
        }
      );

      if (response.ok) {
        setUser(await response.json());
      }
    } finally {
      setInitialized(true);
    }
  };

  useEffect(() => {
    loadUser();
  }, []);

  const login = () => {
    window.location.href = `${AUTH_URL}/auth/login`;
  };

  const logout = async () => {
    await fetch(
      `${AUTH_URL}/auth/logout`,
      {
        method: 'POST',
        credentials: 'include',
      }
    );

    setUser(null);
  };

  const downloadReport = async () => {
    try {
      setLoading(true);
      setError(null);

      const response = await fetch(
          `${AUTH_URL}/reports`,
        {
          credentials: 'include',
      });

    if (!response.ok) {
      throw new Error(`Failed to download report: ${response.status}`);
    }

    const blob = await response.blob();

    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');

    link.href = url;
    link.download = 'report.xlsx'; // поменяй расширение, если отчет PDF/CSV
    document.body.appendChild(link);

    link.click();
    link.remove();

    window.URL.revokeObjectURL(url);
  } catch (err) {
    setError(
      err instanceof Error
        ? err.message
        : 'An error occurred'
    );
  } finally {
    setLoading(false);
  }
};

  if (!initialized) {
    return <div>Loading...</div>;
  }

  if (!user) {
    return (
        <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">

        <button
          onClick={login}
          className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600"
        >
          Login
        </button>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white rounded-lg shadow-md">
        <h1 className="text-2xl font-bold mb-6">Usage Reports</h1>

        <button
          onClick={downloadReport}
          disabled={loading}
          className={`px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 ${
            loading ? 'opacity-50 cursor-not-allowed' : ''
          }`}
        >
          {loading ? 'Generating Report...' : 'Download Report'}
        </button>

        {error && (
          <div className="mt-4 p-4 bg-red-100 text-red-700 rounded">
            {error}
          </div>
        )}
      </div>
    </div>
  );
};

export default ReportPage;