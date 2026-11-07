import { Heart } from 'lucide-react';
import { useFavouriteClub } from '../../lib/personal';
import { Button } from '../ui';

/** Marks a club as the visitor's own (browser only); the home page then shows its next fixture. */
export function FavouriteClubButton({ team }: { team: string }) {
  const { club, toggle } = useFavouriteClub();
  const mine = club === team;
  return (
    <Button
      variant="secondary"
      size="sm"
      aria-pressed={mine}
      title="Kept in this browser only"
      onClick={() => toggle(team)}
      icon={<Heart className={mine ? 'h-4 w-4 fill-current text-accent' : 'h-4 w-4'} aria-hidden="true" />}
    >
      {mine ? 'My club' : 'Make this my club'}
    </Button>
  );
}
