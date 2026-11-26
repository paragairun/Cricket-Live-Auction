"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import api from "@/lib/api";

interface Event {
  id: number;
  name: string;
  status: string;
  allowed_domains: string[];
}

export default function OrganizerEventsPage() {
  const router = useRouter();
  const [events, setEvents] = useState<Event[]>([]);
  const [loading, setLoading] = useState(true);
  const [creatingPracticeFor, setCreatingPracticeFor] = useState<number | null>(null);
  const [deletingEventId, setDeletingEventId] = useState<number | null>(null);

  useEffect(() => {
    api
      .get("/organizer/events")
      .then(({ data }) => setEvents(data))
      .catch((err) => {
        // Only redirect on 401 auth errors; other errors stay visible
        if (err.response?.status === 401) {
          router.push("/auth/login");
        }
      })
      .finally(() => setLoading(false));
  }, [router]);

  const deleteEvent = async (event: Event) => {
    const confirmed = window.confirm(
      `Delete "${event.name}"? The event will be archived and hidden from event lists. Its auction results will be preserved.`,
    );
    if (!confirmed) return;

    setDeletingEventId(event.id);
    try {
      await api.delete(`/organizer/events/${event.id}`);
      setEvents((current) => current.filter((item) => item.id !== event.id));
    } catch (error: any) {
      alert(error.response?.data?.detail || "Could not delete this event.");
    } finally {
      setDeletingEventId(null);
    }
  };

  const createPracticeCopy = async (eventId: number) => {
    setCreatingPracticeFor(eventId);
    try {
      const { data } = await api.post(`/organizer/events/${eventId}/practice-copy`);
      router.push(`/organizer/events/${data.id}`);
    } catch (error: any) {
      alert(error.response?.data?.detail || "Could not create practice auction.");
    } finally {
      setCreatingPracticeFor(null);
    }
  };

  return (
    <div className="min-h-screen p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-4 mb-8">
          <a href="/dashboard" className="text-gray-500 hover:text-white">← Dashboard</a>
          <h1 className="text-2xl font-bold">My Events</h1>
        </div>

        {loading ? (
          <p className="text-gray-500">Loading...</p>
        ) : events.length === 0 ? (
          <div className="card text-center text-gray-500 py-10">
            No events assigned. Ask admin to assign you as organizer.
          </div>
        ) : (
          <div className="space-y-4">
            {events.map((event) => (
              <div key={event.id} className="card flex items-center justify-between gap-4">
                <div>
                  <p className="font-semibold">{event.name}</p>
                  <p className="text-sm text-gray-500">
                    Status: <span className="capitalize text-amber-400">{event.status}</span>
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <button
                    className="btn-secondary text-sm"
                    onClick={() => router.push(`/organizer/events/${event.id}`)}
                  >
                    Setup
                  </button>
                  <button
                    className="btn-secondary text-sm"
                    disabled={creatingPracticeFor !== null}
                    onClick={() => void createPracticeCopy(event.id)}
                  >
                    {creatingPracticeFor === event.id ? "Creating..." : "Create Practice Copy"}
                  </button>
                  <button
                    className="btn-secondary text-sm text-red-400 hover:text-red-300"
                    disabled={deletingEventId !== null || creatingPracticeFor !== null}
                    onClick={() => void deleteEvent(event)}
                  >
                    {deletingEventId === event.id ? "Deleting..." : "Delete"}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
