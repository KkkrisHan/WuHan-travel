import { IconMapPin, IconTrash } from "@tabler/icons-react";
import type { Place } from "../types";

export function MustVisitPanel({ places, onSelect, onRemove, onGenerate }: {
  places: Place[];
  onSelect: (place: Place) => void;
  onRemove: (place: Place) => void;
  onGenerate: () => void;
}) {
  return <section className="must-visit-panel">
    <header className="panel-heading itinerary-heading"><div><div className="panel-kicker"><IconMapPin size={15}/> 必去景点</div><h2>这趟旅行想去的地方</h2></div></header>
    <div className="must-visit-list">
      {!places.length ? <div className="empty-state"><IconMapPin size={25}/><strong>还没有必去景点</strong><p>从地图地点卡中加入想去的地点。</p></div> : places.map((place) => <article className="must-visit-item" key={place.placeId}>
        <button className="must-visit-main" onClick={() => onSelect(place)}><strong>{place.name}</strong><span>{place.category} · {place.address}</span></button>
        <button className="icon-button" aria-label={`移除${place.name}`} onClick={() => onRemove(place)}><IconTrash size={17}/></button>
      </article>)}
    </div>
    <footer className="must-visit-footer"><button className="button primary" disabled={!places.length} onClick={onGenerate}>生成行程</button></footer>
  </section>;
}
