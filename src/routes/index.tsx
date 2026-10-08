import { createFileRoute } from "@tanstack/react-router";
import { Mission } from "@/components/mission";

export const Route = createFileRoute("/")({ component: Home });

function Home() {
  return <Mission />;
}
